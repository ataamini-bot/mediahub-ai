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
            "delivery_state": row.delivery_state}


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
    if existing:
        if existing.user_id != user.id or existing.kind != data.kind or existing.options != options:
            raise MediaToolError("request_conflict")
        return existing, False
    config = await read_config(db, data.kind)
    if not config["enabled"] or not config["token"]:
        raise MediaToolError("tool_disabled", 503)
    from app.services.download_access import quota_day_start_utc
    midnight = quota_day_start_utc(timezone_name=await get_managed_setting(db, "quota.timezone"))
    totals = await db.execute(select(func.count(), func.count().filter(MediaToolRequest.user_id == user.id)).where(
        MediaToolRequest.kind == data.kind, MediaToolRequest.created_at >= midnight))
    total, personal = totals.one()
    if total >= config["global_daily"] or personal >= config["per_user_daily"]:
        raise MediaToolError("tool_daily_limit", 429)
    if await db.scalar(select(MediaToolRequest.id).where(MediaToolRequest.user_id == user.id,
            MediaToolRequest.status.in_(("pending", "processing"))).limit(1)):
        raise MediaToolError("tool_busy")
    try:
        input_file(data.telegram_id, data.request_id, data.kind)
    except ValueError:
        raise MediaToolError("input_expired", 422) from None
    row = MediaToolRequest(id=data.request_id, user_id=user.id, kind=data.kind, options=options, status="pending")
    db.add(row)
    await db.commit()
    return row, True


async def reconcile_restored_tools(db):
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
