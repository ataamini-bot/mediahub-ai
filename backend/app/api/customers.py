from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.services.admin_access import AdminAccessDenied
from app.services.customers import CustomerConflict, CustomerService
from app.api.operations import actor
from app.models.plan import Plan

router = APIRouter(prefix="/admin/customers", tags=["customers"], dependencies=[Depends(require_internal_api_key)])


class CustomerChange(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    request_id: UUID
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["block", "unblock", "note", "reset_quota", "grant", "extend", "cancel", "change_plan"]
    reason: str = Field(min_length=5, max_length=500)
    plan_id: int | None = Field(default=None, gt=0)
    subscription_id: int | None = Field(default=None, gt=0)
    days: int | None = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_action(self):
        self.reason = self.reason.strip()
        if len(self.reason) < 5:
            raise ValueError("A meaningful reason is required")
        if self.action in {"grant", "change_plan"} and self.plan_id is None:
            raise ValueError("Plan required")
        if self.action in {"extend", "cancel", "change_plan"} and self.subscription_id is None:
            raise ValueError("Subscription required")
        if self.action in {"grant", "extend"} and self.days is None:
            raise ValueError("Days required")
        return self


@router.get("")
async def search(actor_telegram_id: int = Query(gt=0), q: str = Query(min_length=1, max_length=100),
                 page: int = Query(default=1, ge=1), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, "users.view")
    return await CustomerService(db).search(q, page)


@router.get("/plans")
async def plans(actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, "subscriptions.manage")
    rows = (await db.scalars(select(Plan).where(Plan.is_active.is_(True), Plan.deleted_at.is_(None),
                                               Plan.slug != "free").order_by(Plan.sort_order, Plan.id))).all()
    return [{"id": row.id, "name": row.name, "name_en": row.name_en} for row in rows]


@router.get("/{telegram_id}")
async def profile(telegram_id: int, actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    context = await actor(db, actor_telegram_id, "users.view")
    try:
        result = await CustomerService(db).profile(telegram_id)
        result["can_manage_users"] = context.has_permission("users.manage")
        result["can_manage_subscriptions"] = context.has_permission("subscriptions.manage")
        result["can_manage_credit"] = context.has_permission("balances.manage")
        return result
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{telegram_id}/downloads")
async def download_activity(telegram_id: int, actor_telegram_id: int = Query(gt=0),
                            page: int = Query(default=1, ge=1), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, "users.view")
    try:
        return await CustomerService(db).download_activity(telegram_id, page)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{telegram_id}/actions")
async def change(telegram_id: int, data: CustomerChange, db: AsyncSession = Depends(get_db)):
    try:
        return await CustomerService(db).change(telegram_id, data)
    except AdminAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except CustomerConflict as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
