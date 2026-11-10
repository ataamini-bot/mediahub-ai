"""Delete temporary media bytes; retain source links and activity in PostgreSQL."""
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, func, or_, select, text

from app.models.download_job import DownloadJob, DownloadJobStatus as Status

log = logging.getLogger(__name__)
TERMINAL = (Status.COMPLETED, Status.FAILED, Status.CANCELLED, Status.EXPIRED)


def media_root():
    return Path(os.getenv("DOWNLOAD_DIR", "/app/downloads")).resolve()


def unlink_inside(path, root):
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    if path.is_symlink():
        if path.parent.resolve().is_relative_to(root):
            path.unlink(missing_ok=True)
        return
    if path.resolve().is_relative_to(root) and path.is_file():
        path.unlink(missing_ok=True)


def remove_files(job):
    root = media_root()
    for path in root.glob(f"{int(job.id)}.*"):
        unlink_inside(path, root)
    if job.file_path:
        unlink_inside(job.file_path, root)
    source = str(job.source_url or "")
    if source.startswith("upload://"):
        name = source.removeprefix("upload://")
        if re.fullmatch(r"upload-[A-Za-z0-9._-]+", name):
            unlink_inside(root / "incoming" / name, root)


def release_files(session, job, *, now=None):
    """Caller holds the row lock. No activity or financial fields are erased."""
    if job.status not in TERMINAL or job.files_removed_at is not None:
        return False
    remove_files(job)
    job.files_removed_at = now or datetime.now(timezone.utc)
    session.flush()
    return True


def sweep(session, *, now=None, limit=200):
    now = now or datetime.now(timezone.utc)
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(764839216)")):
        return 0
    jobs = session.scalars(select(DownloadJob).where(DownloadJob.files_removed_at.is_(None), or_(
        DownloadJob.status.in_((Status.FAILED, Status.CANCELLED, Status.EXPIRED)),
        and_(DownloadJob.status == Status.COMPLETED, or_(DownloadJob.delivered_at.is_not(None),
            func.coalesce(DownloadJob.completed_at, DownloadJob.created_at) < now-timedelta(hours=24)))
    )).order_by(DownloadJob.id).limit(limit).with_for_update(skip_locked=True)).all()
    removed = 0
    for job in jobs:
        try:
            with session.begin_nested():
                removed += release_files(session, job, now=now)
        except OSError as exc:
            log.warning("Media file cleanup failed for job %s: %s", job.id, type(exc).__name__)
    return removed


def remove_orphans(session, *, now=None):
    now = now or datetime.now(timezone.utc)
    root = media_root()
    active = session.execute(select(DownloadJob.id, DownloadJob.source_url).where(DownloadJob.files_removed_at.is_(None))).all()
    ids = {str(row[0]) for row in active}
    uploads = {str(row[1]).removeprefix("upload://") for row in active if str(row[1]).startswith("upload://")}
    cutoff = (now - timedelta(hours=1)).timestamp()
    for directory in (root, root / "incoming"):
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.iterdir():
            match = re.match(r"^([1-9][0-9]*)[.]", path.name)
            orphan = (match and match[1] not in ids) if directory == root else (
                re.fullmatch(r"upload-[A-Za-z0-9._-]+", path.name) and path.name not in uploads)
            if directory == root and re.fullmatch(r"cover[-_][A-Za-z0-9._-]+", path.name):
                orphan = True
            if orphan:
                try:
                    if path.lstat().st_mtime < cutoff:
                        unlink_inside(path, root)
                except FileNotFoundError:
                    pass


async def cleanup_loop():
    from app.db.session import AsyncSessionLocal
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await db.run_sync(sweep)
                await db.commit()
                await db.run_sync(remove_orphans)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Media file cleanup unavailable: %s", type(exc).__name__)
        await asyncio.sleep(30)
