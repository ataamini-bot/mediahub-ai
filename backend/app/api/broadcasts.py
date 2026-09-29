from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations import actor
from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.models.broadcast import Broadcast, BroadcastRecipient
from app.models.plan import Plan
from app.schemas.broadcast import BroadcastAck, BroadcastActor, BroadcastConfirm, BroadcastCreate
from app.services.broadcasts import BroadcastConflict, BroadcastService, PERMISSION

router = APIRouter(tags=["broadcasts"], dependencies=[Depends(require_internal_api_key)])


async def mutate(db, operation):
    try:
        result = await operation
        await db.flush()
        payload = await BroadcastService(db).detail(result) if isinstance(result, Broadcast) else result
        await db.commit()
        return payload
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except BroadcastConflict as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.get("/admin/broadcasts/plans")
async def plans(actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, PERMISSION)
    rows = (await db.scalars(select(Plan).where(Plan.is_active.is_(True), Plan.deleted_at.is_(None),
                                               Plan.slug != "free").order_by(Plan.sort_order, Plan.id))).all()
    return [{"id": p.id, "name": p.name, "name_en": p.name_en} for p in rows]


@router.get("/admin/broadcasts")
async def history(actor_telegram_id: int = Query(gt=0), page: int = Query(default=1, ge=1),
                  db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, PERMISSION)
    rows = (await db.scalars(select(Broadcast).order_by(Broadcast.id.desc()).offset((page-1)*8).limit(8))).all()
    return {"total": await db.scalar(select(func.count()).select_from(Broadcast)),
            "items": [await BroadcastService(db).detail(job) for job in rows], "page": page}


@router.get("/admin/broadcasts/{broadcast_id}")
async def detail(broadcast_id: int, actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, PERMISSION)
    try:
        return await BroadcastService(db).detail(await BroadcastService(db).load(broadcast_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/admin/broadcasts/{broadcast_id}/issues")
async def issues(broadcast_id: int, actor_telegram_id: int = Query(gt=0), page: int = Query(default=1, ge=1),
                 db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, PERMISSION)
    condition = (BroadcastRecipient.broadcast_id == broadcast_id,
                 BroadcastRecipient.status.in_(["blocked", "failed", "uncertain", "skipped"]))
    rows = (await db.scalars(select(BroadcastRecipient).where(*condition)
        .order_by(BroadcastRecipient.id).offset((page-1)*15).limit(15))).all()
    return {"total": await db.scalar(select(func.count()).select_from(BroadcastRecipient).where(*condition)),
            "items": [{"telegram_id": item.telegram_id, "status": item.status, "error_code": item.error_code}
                      for item in rows], "page": page}


@router.post("/admin/broadcasts")
async def create(data: BroadcastCreate, db: AsyncSession = Depends(get_db)):
    await actor(db, data.actor_telegram_id, PERMISSION)
    return await mutate(db, BroadcastService(db).create(data))


@router.post("/admin/broadcasts/{broadcast_id}/confirm")
async def confirm(broadcast_id: int, data: BroadcastConfirm, db: AsyncSession = Depends(get_db)):
    await actor(db, data.actor_telegram_id, PERMISSION)
    return await mutate(db, BroadcastService(db).confirm(broadcast_id, data))


@router.post("/admin/broadcasts/{broadcast_id}/{action}")
async def control(broadcast_id: int, action: Literal["pause", "resume", "cancel"], data: BroadcastActor,
                  db: AsyncSession = Depends(get_db)):
    await actor(db, data.actor_telegram_id, PERMISSION)
    return await mutate(db, BroadcastService(db).control(broadcast_id, data.actor_telegram_id, action))


@router.post("/internal/broadcasts/claim")
async def claim(db: AsyncSession = Depends(get_db)):
    return await mutate(db, BroadcastService(db).claim())


@router.post("/internal/broadcasts/{recipient_id}/ack")
async def acknowledge(recipient_id: int, data: BroadcastAck, db: AsyncSession = Depends(get_db)):
    return await mutate(db, BroadcastService(db).acknowledge(recipient_id, data))
