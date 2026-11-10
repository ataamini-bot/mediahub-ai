"""Local console bootstrap. This module is deliberately not an HTTP endpoint."""
import asyncio
import json
import sys

from sqlalchemy import select, update

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.bot_experience import RequiredChannel
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.models.user import User
from app.services.admin_access import AdminAccessService
from app.services.application_settings import ApplicationSettingsService
from app.services.audit import AuditService
from app.services.bot_experience import BotExperienceService
from app.services.operations import TOPICS, notification_routes


async def configure(db, payload):
    action = payload.get("action")
    if action == "telegram_api":
        # Local Bot API is private to the Compose network. Do not switch this
        # bot back to the public server while it is polling the local server.
        import urllib.request
        import urllib.error
        if payload["method"] not in {"getMe", "getChat", "getChatMember", "createForumTopic"}:
            raise ValueError("Unsupported setup method")
        request = urllib.request.Request(
            "http://telegram-api:8081/bot" + settings.telegram_bot_token + "/" + payload["method"],
            data=json.dumps(payload.get("parameters", {})).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            result = json.load(exc)
            result["description"] = str(result.get("description", "request failed")).replace(settings.telegram_bot_token, "[hidden]")
            return result
    if action == "routes":
        return await notification_routes(db)
    if action == "reconcile_restore":
        from app.services.broadcasts import cancel_restored_broadcasts
        from app.services.credit import reconcile_credit_notices_after_restore
        result = await db.execute(update(DownloadJob).where(DownloadJob.status.in_([
            DownloadJobStatus.PENDING, DownloadJobStatus.PROCESSING, DownloadJobStatus.PAUSED
        ])).values(status=DownloadJobStatus.CANCELLED))
        broadcasts = await cancel_restored_broadcasts(db)
        notices = await reconcile_credit_notices_after_restore(db)
        AuditService(db).record(action="system.database_restored", target_type="database",
            details={"unfinished_jobs_cancelled": result.rowcount, "credit_notices_uncertain": notices, **broadcasts})
        return {"reconciled": True}
    telegram_id = settings.telegram_superadmin_id
    if not telegram_id:
        raise ValueError("TELEGRAM_SUPERADMIN_ID is required for installation")
    user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id)
        db.add(user)
        await db.flush()
    access = AdminAccessService(db)
    await access.ensure_bootstrap_superadmin(user)
    await db.flush()
    context = await access.get_context(telegram_id)
    if not context.is_superadmin:
        raise ValueError("An active superadmin is required")
    if action == "bootstrap":
        return {"bootstrap": True}
    if action == "forum":
        routes = payload["routes"]
        if set(routes["topics"]) != set(TOPICS):
            raise ValueError("All notification topics are required")
        changes = {"notifications.chat_id": routes["chat_id"], "notifications.enabled": True,
                   **{f"notifications.topic.{name}": routes["topics"][name] for name in TOPICS}}
        service = ApplicationSettingsService(db)
        for key, value in changes.items():
            await service.set_value(key=key, category="notifications", value=value, is_sensitive=False,
                                    actor_user_id=user.id, actor_telegram_id=telegram_id)
        return {"configured": True}
    if action == "channel":
        data = payload["channel"]
        row = await db.scalar(select(RequiredChannel).where(RequiredChannel.chat_id == data["chat_id"]))
        service = BotExperienceService(db)
        if row:
            await service.update_channel(channel_id=row.id, changes=data, actor_user_id=user.id,
                                         actor_telegram_id=telegram_id)
        else:
            await service.create_channel(data=data, actor_user_id=user.id, actor_telegram_id=telegram_id)
        return {"configured": True}
    raise ValueError("Unknown installation action")


async def main():
    payload = json.load(sys.stdin)
    try:
        async with AsyncSessionLocal() as db:
            result = await configure(db, payload)
            await db.commit()
        print(json.dumps(result))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
