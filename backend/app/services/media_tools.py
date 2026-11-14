"""Admission, ownership and retention for bounded external tool attempts."""
from datetime import datetime, timedelta, timezone
import re
from uuid import UUID

from sqlalchemy import func, or_, select, text, update

from app.models.media_tool import MediaToolRequest
from app.models.user import User, UserStatus
from app.services.managed_settings import ensure_public_operation, get_managed_setting
from app.services.media_tool_config import read_config
from app.services.media_tool_files import folder, input_file, remove_files, root


class MediaToolError(RuntimeError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


async def owned(db, request_id, telegram_id, *, lock=False):
    query = select(MediaToolRequest).join(User, User.id == MediaToolRequest.user_id).where(
        MediaToolRequest.id == request_id, User.telegram_id == telegram_id, User.status == UserStatus.ACTIVE)
    if lock:
        query = query.with_for_update(of=MediaToolRequest).execution_options(populate_existing=True)
    row = await db.scalar(query)
    if row is None:
        raise MediaToolError("not_found", 404)
    return row


def present(row):
    return {"id": str(row.id), "kind": row.kind, "status": row.status, "result": row.result,
            "error_code": row.error_code, "files_removed": row.files_removed_at is not None,
            "download_job_id": row.download_job_id, "download_state": row.download_state,
            "delivery_state": row.delivery_state, "auto_mp3": bool((row.options or {}).get("auto_mp3"))}


async def submit(db, data):
    await ensure_public_operation(db, "downloads")
    # One admission lock makes the global and per-user daily limits atomic,
    # including two different tools submitted concurrently by the same user.
    await db.execute(text("SELECT pg_advisory_xact_lock(764839225)"))
    user = await db.scalar(select(User).where(User.telegram_id == data.telegram_id))
    if user is None or user.status != UserStatus.ACTIVE:
        raise MediaToolError("access_denied", 403)
    existing = await db.get(MediaToolRequest, data.request_id)
    options = {"clothing": data.clothing, "background": data.background} if data.kind == "portrait" else {}
    if data.use_paid_credit:
        if data.kind != "portrait":
            raise MediaToolError("request_conflict", 422)
        options["use_paid_credit"] = True
    if data.kind == "music" and data.auto_mp3:
        options["auto_mp3"] = True
    if data.source_url is not None:
        from app.services.media_tool_links import normalize_music_link
        if data.kind != "music":
            raise MediaToolError("invalid_music_link", 422)
        try:
            options["source_url"] = normalize_music_link(data.source_url)
        except ValueError:
            raise MediaToolError("invalid_music_link", 422) from None
    if existing:
        if existing.user_id != user.id or existing.kind != data.kind or existing.options != options:
            raise MediaToolError("request_conflict")
        return existing, False
    config = await read_config(db, data.kind)
    if not config["enabled"] or not config["token"]:
        raise MediaToolError("tool_disabled", 503)
    from app.services.download_access import quota_day_start_utc, quota_window_start_utc
    timezone_name = await get_managed_setting(db, "quota.timezone")
    midnight = quota_day_start_utc(timezone_name=timezone_name)
    totals = await db.execute(select(func.count()).where(
        MediaToolRequest.kind == data.kind, MediaToolRequest.created_at >= midnight))
    if totals.scalar_one() >= config["global_daily"]:
        raise MediaToolError("tool_global_limit", 429)
    credit = None
    if data.use_paid_credit:
        from app.models.portrait_credit import PortraitCredit
        credit = await db.scalar(select(PortraitCredit).where(PortraitCredit.user_id == user.id,
            PortraitCredit.request_id.is_(None)).order_by(PortraitCredit.created_at).limit(1).with_for_update())
        if credit is None:
            raise MediaToolError("portrait_credit_missing", 409)
    else:
        limit, period = await plan_tool_limit(db, user.telegram_id, data.kind, config)
        if limit == 0:
            raise MediaToolError("tool_plan_disabled", 403)
        window = quota_window_start_utc(period, timezone_name=timezone_name)
        personal = await db.scalar(select(func.count()).where(MediaToolRequest.kind == data.kind,
            MediaToolRequest.user_id == user.id, MediaToolRequest.created_at >= window,
            ~MediaToolRequest.options["use_paid_credit"].as_boolean().is_(True)))
        if limit != -1 and personal >= limit:
            if period == "weekly":
                raise MediaToolError("tool_weekly_limit", 429)
            raise MediaToolError("tool_daily_limit", 429)
    if await db.scalar(select(MediaToolRequest.id).where(MediaToolRequest.user_id == user.id,
            MediaToolRequest.status.in_(("pending", "processing"))).limit(1)):
        raise MediaToolError("tool_busy")
    if "source_url" not in options:
        try:
            input_file(data.telegram_id, data.request_id, data.kind)
        except ValueError:
            raise MediaToolError("input_expired", 422) from None
    row = MediaToolRequest(id=data.request_id, user_id=user.id, kind=data.kind, options=options, status="pending")
    db.add(row)
    await db.flush()
    if credit is not None:
        credit.request_id = row.id
    await db.commit()
    return row, True


async def plan_tool_limit(db, telegram_id, kind, config):
    from app.models.plan import Plan
    from app.services.download_access import DownloadAccessService, DownloadAccessError
    try:
        entitlement = await DownloadAccessService(db).get_entitlement(telegram_id)
    except DownloadAccessError:
        raise MediaToolError("tool_plan_unavailable", 503) from None
    plan = await db.get(Plan, entitlement.plan_id) if entitlement.plan_id else None
    configured = (plan.tool_limits or {}).get(kind) if plan else None
    if configured is None:
        return config["per_user_daily"], "daily"
    return configured["limit"], configured.get("period", "daily")


async def reconcile_restored_tools(db):
    from app.models.portrait_credit import PortraitCredit
    unstarted = select(MediaToolRequest.id).where(
        MediaToolRequest.status == "pending", MediaToolRequest.started_at.is_(None))
    await db.execute(update(PortraitCredit).where(
        PortraitCredit.request_id.in_(unstarted)).values(request_id=None))
    # Restored inputs are intentionally absent from backups. In particular a
    # previously processing paid request must never be billed again.
    result = await db.execute(update(MediaToolRequest).where(MediaToolRequest.status.in_(
        ("pending", "processing", "completed"))).values(status="cancelled", error_code="restored_backup"))
    return result.rowcount


def publish_tool(request_id):
    from app.workers.tasks.media_tools import run_tool
    run_tool.apply_async(args=[str(request_id)], task_id=f"media-tools-{request_id}", retry=False)


def dispatch_pending_tools():
    """Recover an admission committed before a broker acknowledgement was saved.

    Re-publication after a lost ACK is safe because the Worker claims once.
    Never re-publish a processing/uncertain request after a worker interruption.
    """
    from app.db.worker_session import WorkerSessionLocal
    with WorkerSessionLocal() as db:
        rows = db.scalars(select(MediaToolRequest).where(MediaToolRequest.status == "pending",
            or_(MediaToolRequest.enqueued_at.is_(None), MediaToolRequest.enqueued_at <
                datetime.now(timezone.utc) - timedelta(minutes=2))).order_by(MediaToolRequest.created_at).limit(20)
            .with_for_update(skip_locked=True)).all()
        for row in rows:
            try:
                publish_tool(row.id)
            except Exception:
                break
            row.enqueued_at = datetime.now(timezone.utc)
        db.commit()


def sweep_tools(db, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(764839226)")):
        return
    rows = db.execute(select(MediaToolRequest, User.telegram_id).join(User, User.id == MediaToolRequest.user_id)
        .where(MediaToolRequest.files_removed_at.is_(None)).with_for_update(of=MediaToolRequest, skip_locked=True)).all()
    active = set()
    for row, telegram_id in rows:
        stale = now - row.created_at > timedelta(minutes=15)
        if row.status in {"pending", "processing"} and stale:
            row.status = "uncertain" if row.started_at else "failed"
            row.error_code = "provider_uncertain" if row.started_at else "queue_expired"
            row.completed_at = now
            from app.services.portrait_purchases import refund_failed_credit
            refund_failed_credit(db, row)
        if row.status in {"pending", "processing"}:
            active.add(folder(telegram_id, row.id).name)
            continue
        if row.status == "completed" and now - (row.completed_at or row.created_at) < timedelta(minutes=30):
            active.add(folder(telegram_id, row.id).name)
            continue
        try:
            remove_files(telegram_id, row.id)
            row.files_removed_at = now
            if row.status == "completed" and row.kind == "portrait":
                row.status = "expired"
        except OSError:
            continue
    # Unconfirmed uploads are never queued; remove them after one hour.
    base = root()
    if base.is_dir() and not base.is_symlink():
        for path in base.iterdir():
            match = re.fullmatch(r"([1-9][0-9]*)-([a-f0-9]{32})", path.name)
            if match and not path.is_symlink() and path.name not in active:
                try:
                    if path.stat().st_mtime < (now - timedelta(hours=1)).timestamp():
                        remove_files(int(match[1]), UUID(match[2]))
                except (FileNotFoundError, OSError):
                    pass
