import asyncio
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.services.admin_access import AdminAccessService, AdminAccessDenied
from app.services.application_settings import ApplicationSettingsService, SettingConflict
from app.services.audit import AuditService
from app.services.operations import TOPICS, MONITOR_DEFAULTS, notification_routes, validate_operation

router = APIRouter(dependencies=[Depends(require_internal_api_key)], tags=["operations"])


async def actor(db, telegram_id, permission):
    try:
        return await AdminAccessService(db).require_permission(telegram_id, permission)
    except AdminAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/internal/notification-routes")
async def routes(db: AsyncSession = Depends(get_db)):
    return await notification_routes(db)


@router.get("/admin/operations")
async def operations(actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    context = await actor(db, actor_telegram_id, "monitoring.view")
    settings = await ApplicationSettingsService(db).list_settings()
    rows = [{"key": row.key, "value": row.value_json, "version": row.version}
            for row in settings if row.category in {"notifications", "monitoring"}]
    snapshot = None
    try:
        async with Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), socket_timeout=2, socket_connect_timeout=2) as client:
            raw = await client.get("mediahub:monitor:snapshot")
            snapshot = json.loads(raw) if raw else None
    except Exception:
        pass
    return {"settings": rows, "routes": await notification_routes(db), "snapshot": snapshot,
            "can_manage": context.is_superadmin or "monitoring.manage" in context.permissions,
            "is_superadmin": context.is_superadmin}


class OperationChange(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    value: int | bool
    expected_version: int = Field(ge=1)


@router.put("/admin/operations/{key}")
async def change(key: str, data: OperationChange, db: AsyncSession = Depends(get_db)):
    context = await actor(db, data.actor_telegram_id, "monitoring.manage")
    if key.startswith("notifications.") and not context.is_superadmin:
        raise HTTPException(403, "Only a superadmin can change notification destinations")
    try:
        value = validate_operation(key, data.value)
        row = await ApplicationSettingsService(db).set_value(
            key=key, category="notifications" if key.startswith("notifications.") else "monitoring",
            value=value, is_sensitive=False, actor_user_id=context.user_id,
            actor_telegram_id=data.actor_telegram_id, expected_version=data.expected_version,
        )
        await db.commit()
        return {"key": row.key, "value": row.value_json, "version": row.version}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except SettingConflict as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/admin/operations/test/{topic}")
async def test_topic(topic: str, actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    context = await actor(db, actor_telegram_id, "monitoring.manage")
    if topic not in TOPICS:
        raise HTTPException(404, "Unknown notification topic")
    routes = await notification_routes(db)
    if not routes["chat_id"] or not routes["topics"].get(topic):
        raise HTTPException(409, "Configure the group and this Topic first")
    from app.services.admin_notifications import send_admin_notification
    sent = await asyncio.to_thread(send_admin_notification, topic,
        f"MediaHub AI — {topic} Topic test requested by {actor_telegram_id}",
        routes_override={**routes, "enabled": True})
    AuditService(db).record(action="notification.test", actor_user_id=context.user_id,
        actor_telegram_id=actor_telegram_id, target_type="notification_topic", target_id=topic,
        details={"delivered": sent}, success=sent)
    await db.commit()
    if not sent:
        raise HTTPException(502, "Telegram rejected the test message; check bot access and Topic id")
    return {"sent": True, "topic": topic}
