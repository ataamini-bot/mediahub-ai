from datetime import datetime, timezone
import logging
import time
from uuid import UUID

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.db.worker_session import WorkerSessionLocal
from app.models.media_tool import MediaToolRequest
from app.models.user import User, UserStatus
from app.services.media_tool_config import read_worker_config
from app.services.media_tool_files import folder, input_file, remove_files
from app.services.media_tool_providers import ToolError, automatic_song_candidate, create_portrait, find_full_tracks, recognize_music
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="media_tools.run", acks_late=True, reject_on_worker_lost=False,
                 soft_time_limit=300, time_limit=330, max_retries=0, ignore_result=True)
def run_tool(request_id):
    request_id = UUID(request_id)
    with WorkerSessionLocal() as db:
        row = db.scalar(select(MediaToolRequest).where(MediaToolRequest.id == request_id).with_for_update())
        # Claim is durable before the provider sees any bytes. A redelivery or
        # a worker crash cannot initiate the same external call twice.
        if not row or row.status != "pending":
            return
        user = db.get(User, row.user_id)
        config = read_worker_config(db, row.kind)
        if not user or user.status != UserStatus.ACTIVE or not config["enabled"] or not config["token"]:
            row.status, row.error_code = "cancelled", "tool_disabled"
            from app.services.portrait_purchases import refund_failed_credit
            refund_failed_credit(db, row)
            db.commit()
            if user:
                remove_files(user.telegram_id, row.id)
            return
        row.status, row.started_at = "processing", datetime.now(timezone.utc)
        db.commit()
        telegram_id, kind, options = user.telegram_id, row.kind, row.options
    result, error = None, None
    try:
        if kind == "music" and options.get("source_url"):
            from app.services.media_tool_links import fetch_music_link
            started = time.monotonic()
            source = fetch_music_link(options["source_url"], folder(telegram_id, request_id) / "input")
            logger.warning("Music link sample ready: request=%s, elapsed_s=%.2f", request_id, time.monotonic() - started)
        else:
            source = input_file(telegram_id, request_id, kind)
        if kind == "music":
            # Link extraction can take longer than an upload. A cancellation
            # received during it must stop before any paid recognition call.
            with WorkerSessionLocal() as db:
                if db.scalar(select(MediaToolRequest.status).where(MediaToolRequest.id == request_id)) != "processing":
                    return
            started = time.monotonic()
            result = recognize_music(source, config["token"])
            logger.warning("Music recognition complete: request=%s, elapsed_s=%.2f, duration_known=%s",
                           request_id, time.monotonic() - started, bool(result.get("duration")))
            # Recognition metadata never supplies a downloadable preview.
            started = time.monotonic()
            result["candidates"] = find_full_tracks(result)
            result["automatic_candidate"] = automatic_song_candidate(result, result["candidates"])
            logger.warning("Music sources ready: request=%s, count=%s, automatic=%s, elapsed_s=%.2f",
                           request_id, len(result["candidates"]), result["automatic_candidate"] is not None,
                           time.monotonic() - started)
        else:
            result = create_portrait(source, config, options)
    except ToolError as exc:
        error = exc.code
    except SoftTimeLimitExceeded:
        error = "provider_uncertain"
    except Exception:
        # Do not log provider responses, credentials, photo bytes or file paths.
        error = "tool_failed"
    finally:
        remove_files(telegram_id, request_id, keep_result=bool(result and kind == "portrait" and not error))
    with WorkerSessionLocal() as db:
        row = db.scalar(select(MediaToolRequest).where(MediaToolRequest.id == request_id).with_for_update())
        if not row or row.status != "processing":
            remove_files(telegram_id, request_id)
            return
        row.completed_at = datetime.now(timezone.utc)
        row.result, row.error_code = result if not error else None, error
        row.status = "uncertain" if error == "provider_uncertain" else "failed" if error else "completed"
        if error or kind == "music":
            row.files_removed_at = row.completed_at
        from app.services.portrait_purchases import refund_failed_credit
        refund_failed_credit(db, row)
        db.commit()
