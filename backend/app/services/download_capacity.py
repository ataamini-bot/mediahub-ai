"""Admission, public-output coalescing and confirmed Telegram reuse.

All admissions take the same transaction lock, then the output row. Each
customer still owns a separate job and quota reservation. Followers never read
another customer's local path. Only a successful Telegram response publishes
a file reference; an uncertain send is never repeated automatically.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import and_, delete, func, or_, select, text

from app.core.config import settings
from app.models.download_job import DownloadJob, DownloadJobStatus as Status
from app.models.public_output import PublicOutput
from app.services.download_access import DownloadAccessError, DownloadAccessService
from app.services.managed_settings import get_managed_setting

logger = logging.getLogger(__name__)
ADMISSION_LOCK = 764839231
ACTIVE = (Status.PENDING, Status.PROCESSING, Status.PAUSED)


class DownloadQueueFull(DownloadAccessError):
    code = "download_queue_full"
    status_code = 503


def public_source_key(url):
    """Conservative public-only allowlist; never uploads, stories or login URLs.

    Instagram runs without accounts/cookies. Reviewed music CDNs contain public
    song files. Other platforms (including unlisted YouTube) are not shared.
    """
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port:
            return None
        host = (parsed.hostname or "").lower()
        if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
            match = re.fullmatch(r"/(?:p|reel|tv)/([A-Za-z0-9_-]{5,64})/?", parsed.path)
            if not match:
                return None
            # Preserve content-affecting query values (e.g. carousel selection).
            query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                     if k not in {"igsh", "igshid", "fbclid"} and not k.startswith("utm_")]
            return "instagram:" + match[1] + ("?" + urlencode(sorted(query)) if query else "")
        from app.services.music_source_catalog import source_for_media
        if not parsed.query and not parsed.fragment and source_for_media(url):
            return f"music:{host}{parsed.path}"
    except (ValueError, TypeError):
        pass
    return None


def source_identity(url):
    """Known social links only; no arbitrary file URLs or uploaded media."""
    public = public_source_key(url)
    if public:
        return public
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.port:
            return None
        from app.services.pinterest_links import is_pinterest_host
        domains = {"youtube.com", "youtu.be", "tiktok.com", "twitter.com", "x.com",
                   "threads.com", "threads.net", "facebook.com", "fb.watch"}
        if not is_pinterest_host(host) and not any(host == d or host.endswith("."+d) for d in domains):
            return None
        query = parse_qsl(parts.query, keep_blank_values=True)
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
            if "list" not in dict(query):
                video = (dict(query).get("v") if parts.path == "/watch" else parts.path.rstrip("/").split("/")[-1])
                if video and re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
                    return "youtube:" + video
        if is_pinterest_host(host) and host != "pin.it":
            from app.services.pinterest_links import pin_id
            pin = pin_id(url)
            return "pinterest:" + pin if pin else None
        # Retain all unknown query parameters, including carousel/playlist state.
        return "link:" + host + parts.path + ("?" + urlencode(sorted(query)) if query else "")
    except (ValueError, TypeError):
        return None


def output_key(job, *, verified_public=False):
    source = source_identity(job.source_url)
    if source is None or job.media_type == "convert":
        return None
    public = bool(public_source_key(job.source_url) or getattr(job, "cache_public", False) or verified_public)
    user_id = getattr(job, "user_id", None)
    if not public and not user_id:
        return None
    scope = "public" if public else f"user:{user_id}"
    identity = ["public-output-v2", settings.telegram_bot_token, scope, source,
                job.playlist_index or 1, job.media_type, job.format_id,
                job.quality, job.output_format, job.delivery_kind]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


async def capacity_lock(db):
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMISSION_LOCK})


async def ensure_queue_space(db, *, exclude_id=None):
    await capacity_lock(db)
    limit = await get_managed_setting(db, "downloads.queue_limit")
    query = select(func.count(DownloadJob.id)).where(or_(DownloadJob.status.in_(ACTIVE),
        and_(DownloadJob.status == Status.COMPLETED, DownloadJob.delivered_at.is_(None),
             DownloadJob.files_removed_at.is_(None))))
    if exclude_id is not None:
        query = query.where(DownloadJob.id != exclude_id)
    from app.models.media_tool import MediaToolRequest
    tools = await db.scalar(select(func.count(MediaToolRequest.id)).where(MediaToolRequest.status.in_(("pending", "processing"))))
    if int(await db.scalar(query) or 0) + int(tools or 0) >= limit:
        raise DownloadQueueFull("Download queue is full; please try again shortly")


def _copy_output(job, output):
    maximum = int((job.plan_limits_snapshot or {}).get("max_file_size_mb") or 2000) * 1024 * 1024
    if not output.file_size or output.file_size > maximum:
        return False
    job.telegram_file_id = output.file_id
    job.telegram_file_kind = output.file_kind
    job.telegram_filename = output.filename
    job.file_size = job.total_bytes = job.downloaded_bytes = output.file_size
    job.status, job.progress = Status.COMPLETED, 100
    job.completed_at = datetime.now(timezone.utc)
    job.file_path = None
    job.files_removed_at = None
    return True


def _live_leader(leader, now):
    if leader is None:
        return False
    if leader.status in {Status.PENDING, Status.PROCESSING}:
        return (now - leader.created_at).total_seconds() < 24 * 3600
    return (leader.status == Status.COMPLETED and not leader.files_removed_at
            and leader.completed_at and (now - leader.completed_at).total_seconds() < 300)


async def prepare_job(db, job, entitlement):
    """Return True only for a job that needs its own worker task. No commits."""
    await capacity_lock(db)
    await ensure_queue_space(db, exclude_id=job.id)
    enabled = await get_managed_setting(db, "downloads.public_reuse")
    key = output_key(job) if enabled else None
    now = datetime.now(timezone.utc)
    output = await db.get(PublicOutput, key, with_for_update=True) if key else None
    if output and output.file_id and output.expires_at > now:
        if entitlement is not None:
            DownloadAccessService._validate_estimated_size(entitlement, output.file_size)
        if _copy_output(job, output):
            job.public_output_key = key
            logger.info("Public output reused")
            return False
    if key:
        job.public_output_key = key
        if output and output.expires_at > now and not output.file_id:
            leader = await db.get(DownloadJob, output.leader_job_id) if output.leader_job_id else None
            if leader is not job and _live_leader(leader, now):
                logger.info("Public output request coalesced")
                return False
        if output is None:
            output = PublicOutput(key=key)
            db.add(output)
        output.leader_job_id = job.id
        output.expires_at = now + timedelta(hours=24)
        output.file_id = output.file_kind = output.filename = output.file_size = None
    return True


async def refresh_waiting(db, job):
    """Resolve a follower or replace a cancelled/failed leader exactly once."""
    if job.status != Status.PENDING or not job.public_output_key:
        return False
    await capacity_lock(db)
    await db.refresh(job, with_for_update=True)
    if job.status != Status.PENDING:
        return False
    output = await db.get(PublicOutput, job.public_output_key, with_for_update=True)
    now = datetime.now(timezone.utc)
    if output and output.file_id and output.expires_at > now:
        if not _copy_output(job, output):
            job.status, job.error_message = Status.FAILED, "download_file_size_limit_exceeded"
        await db.commit()
        return False
    if output and output.leader_job_id == job.id:
        return False
    leader = await db.get(DownloadJob, output.leader_job_id) if output and output.leader_job_id else None
    if output and output.expires_at > now and _live_leader(leader, now):
        return False
    if output is None:
        output = PublicOutput(key=job.public_output_key)
        db.add(output)
    output.leader_job_id, output.expires_at = job.id, now + timedelta(hours=24)
    output.file_id = output.file_kind = output.filename = output.file_size = None
    return True


async def publish_job(db, job, priority=False):
    """Commit before dispatch. A lost broker ACK is recovered by the dispatcher."""
    await db.commit()
    from app.workers.tasks.download import download_task
    try:
        task = download_task.apply_async(args=[job.id], priority=0 if priority else 5)
    except Exception:
        logger.warning("Download dispatch deferred")
        return
    job.celery_task_id = task.id
    job.enqueued_at = datetime.now(timezone.utc)
    await db.commit()


async def claim_delivery(db, job_id):
    job = await db.get(DownloadJob, job_id, with_for_update=True, populate_existing=True)
    if job is None or job.status != Status.COMPLETED:
        return {"claimed": False, "reason": "download_not_ready"}
    if job.delivered_at or job.delivery_started_at:
        return {"claimed": False, "reason": "delivery_started"}
    job.delivery_started_at = datetime.now(timezone.utc)
    await db.commit()
    return {"claimed": True}


async def confirm_delivery(db, job_id, receipt=None):
    import asyncio
    from app.services.source_capacity import public_verified
    original = await db.get(DownloadJob, job_id)
    verified = bool(receipt and original and await asyncio.to_thread(public_verified, original.source_url))
    await capacity_lock(db)
    job = await db.get(DownloadJob, job_id, with_for_update=True, populate_existing=True)
    if job is None:
        raise LookupError("Download job not found")
    if job.status != Status.COMPLETED:
        raise ValueError("Only a completed download can be delivered")
    # Existing clients may confirm without a receipt; they cannot publish cache.
    if receipt and job.delivery_started_at and job.public_output_key == output_key(job):
        if receipt.file_kind != job.delivery_kind:
            raise ValueError("Telegram file kind does not match the requested delivery")
        output = await db.get(PublicOutput, job.public_output_key, with_for_update=True)
        enabled = await get_managed_setting(db, "downloads.public_reuse")
        if enabled and output and output.leader_job_id == job.id and not output.file_id:
            output.file_id, output.file_kind = receipt.file_id, receipt.file_kind
            output.filename, output.file_size = receipt.filename, job.file_size
            minutes = await get_managed_setting(db, "downloads.reuse_minutes")
            output.expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            alias_key = output_key(job, verified_public=True) if verified else None
            if alias_key and alias_key != job.public_output_key:
                alias = await db.get(PublicOutput, alias_key, with_for_update=True)
                if alias is None:
                    db.add(PublicOutput(key=alias_key, leader_job_id=job.id, file_id=output.file_id,
                        file_kind=output.file_kind, filename=output.filename, file_size=output.file_size,
                        expires_at=output.expires_at))
    job.delivered_at = job.delivered_at or datetime.now(timezone.utc)
    await db.commit()
    return job


async def invalidate_cached_delivery(db, job_id):
    await capacity_lock(db)
    job = await db.get(DownloadJob, job_id, with_for_update=True)
    if job and job.telegram_file_id and not job.delivered_at:
        output = await db.get(PublicOutput, job.public_output_key, with_for_update=True)
        if output and output.file_id == job.telegram_file_id:
            await db.delete(output)
        job.status, job.error_message = Status.FAILED, "telegram_cached_file_expired"
        job.telegram_file_id = None
        await db.commit()


async def clear_restored_outputs(db):
    await db.execute(delete(PublicOutput))
    # Old Telegram references and unfinished delivery claims must not be replayed.
    from sqlalchemy import update
    await db.execute(update(DownloadJob).where(DownloadJob.telegram_file_id.is_not(None),
        DownloadJob.delivered_at.is_(None), DownloadJob.status == Status.COMPLETED)
        .values(status=Status.CANCELLED, error_message="restored_backup"))
    await db.execute(update(DownloadJob).values(public_output_key=None, telegram_file_id=None,
                                              telegram_file_kind=None, telegram_filename=None))


async def recover_pending_downloads():
    """Recover committed admissions after API/broker interruption, in small batches.

    No processing task or uncertain Telegram delivery is retried here. Duplicate
    pending broker deliveries are harmless because the worker locks and claims
    the job before doing any source/network work.
    """
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        await db.execute(delete(PublicOutput).where(PublicOutput.expires_at < now))
        # Keep reference storage bounded even during sustained unique traffic.
        excess = select(PublicOutput.key).where(PublicOutput.file_id.is_not(None)).order_by(
            PublicOutput.expires_at.desc()).offset(2000)
        await db.execute(delete(PublicOutput).where(PublicOutput.key.in_(excess)))
        ids = list(await db.scalars(select(DownloadJob.id).where(DownloadJob.status == Status.PENDING,
            or_(DownloadJob.enqueued_at.is_(None), DownloadJob.enqueued_at < now - timedelta(minutes=2)))
            .order_by(DownloadJob.created_at).limit(50)))
        await db.commit()
    for job_id in ids:
        async with AsyncSessionLocal() as db:
            await capacity_lock(db)
            job = await db.get(DownloadJob, job_id, with_for_update=True)
            if job is None or job.status != Status.PENDING:
                continue
            promoted = await refresh_waiting(db, job)
            if job.status != Status.PENDING:
                continue
            if job.public_output_key and not promoted:
                output = await db.get(PublicOutput, job.public_output_key)
                if output and output.leader_job_id != job.id:
                    # Move this follower behind newer recoverable admissions.
                    job.enqueued_at = now
                    await db.commit()
                    continue
            if promoted or job.enqueued_at is None or job.enqueued_at < now - timedelta(minutes=2):
                await publish_job(db, job, bool((job.plan_limits_snapshot or {}).get("priority_processing")))
