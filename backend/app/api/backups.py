import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations import actor
from app.backup.engine import DEFAULTS, atomic_json, list_backups, root
from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.services.application_settings import ApplicationSettingsService, SettingConflict
from app.services.audit import AuditService

router = APIRouter(prefix="/admin/backups", tags=["backups"], dependencies=[Depends(require_internal_api_key)])


@router.get("")
async def status(actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    context = await actor(db, actor_telegram_id, "backups.view")
    rows = await ApplicationSettingsService(db).list_settings(category="backups")
    try:
        heartbeat = json.loads((root() / "heartbeat.json").read_text())
    except (OSError, ValueError):
        heartbeat = None
    return {"settings": [{"key": r.key, "value": r.value_json, "version": r.version} for r in rows],
            "items": list_backups()[:10], "heartbeat": heartbeat,
            "can_manage": context.has_permission("backups.manage"),
            "queued": len(list((root() / "requests").glob("*.json")))}


class BackupRequest(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    request_id: UUID


@router.post("/requests")
async def request(data: BackupRequest, db: AsyncSession = Depends(get_db)):
    context = await actor(db, data.actor_telegram_id, "backups.manage")
    backup_id = data.request_id.hex
    directory = root() / "requests"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (backup_id + ".json")
    if path.exists() or (root() / (backup_id + ".json")).exists():
        return {"id": backup_id, "replayed": True}
    if len(list(directory.glob("*.json"))) >= 5:
        raise HTTPException(409, "Backup queue is full; check the backup service")
    # Audit is committed before exposing the request to the worker.
    AuditService(db).record(action="backup.requested", actor_user_id=context.user_id,
        actor_telegram_id=context.telegram_id, target_type="backup", target_id=backup_id)
    await db.commit()
    atomic_json(path, {"id": backup_id})
    return {"id": backup_id, "replayed": False}


class BackupSetting(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    value: int | bool
    expected_version: int = Field(ge=1)


@router.put("/settings/{key}")
async def change(key: str, data: BackupSetting, db: AsyncSession = Depends(get_db)):
    context = await actor(db, data.actor_telegram_id, "backups.manage")
    if key not in DEFAULTS:
        raise HTTPException(404, "Unknown backup setting")
    value = data.value
    if key == "backup.enabled":
        valid = isinstance(value, bool)
    else:
        bounds = {"backup.hour": (0, 23), "backup.daily": (1, 90), "backup.weekly": (1, 52), "backup.monthly": (1, 36)}
        low, high = bounds[key]
        valid = type(value) is int and low <= value <= high
    if not valid:
        raise HTTPException(422, "Invalid backup setting value")
    try:
        row = await ApplicationSettingsService(db).set_value(key=key, category="backups", value=value,
            is_sensitive=False, actor_user_id=context.user_id, actor_telegram_id=context.telegram_id,
            expected_version=data.expected_version)
        await db.commit()
        return {"key": row.key, "value": row.value_json, "version": row.version}
    except SettingConflict as exc:
        await db.rollback()
        raise HTTPException(409, "Setting changed; reopen the backup panel") from exc
