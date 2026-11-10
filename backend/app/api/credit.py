from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations import actor
from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.schemas.credit import CreditChange, CreditNoticeAck
from app.schemas.payment import PaymentOrderActor
from app.services.admin_access import AdminAccessDenied
from app.services.credit import CreditError, CreditService
from app.services.coupons import CouponError
from app.services.payment_orders import PaymentOrderError
from app.services.managed_settings import PublicOperationDisabled

router = APIRouter(tags=["credit"], dependencies=[Depends(require_internal_api_key)])


async def result(db, operation):
    try:
        value = await operation
        await db.commit()
        return value
    except (CouponError, CreditError, PaymentOrderError, AdminAccessDenied, LookupError, PermissionError, PublicOperationDisabled) as exc:
        await db.rollback()
        code = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, LookupError) else 409
        if isinstance(exc, PaymentOrderError):
            raise HTTPException(exc.status_code, exc.detail) from exc
        if isinstance(exc, PublicOperationDisabled):
            raise HTTPException(503, {"code": exc.code}) from exc
        raise HTTPException(code, {"code": exc.code if isinstance(exc, (CreditError, CouponError)) else str(exc)}) from exc


@router.get("/credit/me")
async def mine(telegram_id: int = Query(gt=0), page: int = Query(default=1, ge=1), db: AsyncSession = Depends(get_db)):
    service = CreditService(db)
    async def read():
        user = await service.user(telegram_id)
        currency = "USDT" if user.preferred_language == "en" else "IRT"
        return await service.profile(telegram_id, currency, page=page)
    return await result(db, read())


@router.get("/admin/credit/{telegram_id}")
async def profile(telegram_id: int, actor_telegram_id: int = Query(gt=0),
                  currency: Literal["IRT", "USDT"] = "IRT", page: int = Query(default=1, ge=1),
                  db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, "balances.manage")
    return await result(db, CreditService(db).profile(telegram_id, currency, page=page, admin=True))


@router.post("/admin/credit/{telegram_id}")
async def change(telegram_id: int, data: CreditChange, db: AsyncSession = Depends(get_db)):
    return await result(db, CreditService(db).change(telegram_id, data))


@router.post("/credit/orders/{order_id}/pay")
async def pay(order_id: UUID, data: PaymentOrderActor, db: AsyncSession = Depends(get_db)):
    return await result(db, CreditService(db).purchase(order_id, data.telegram_id))


@router.post("/internal/credit/notices/claim")
async def claim_notice(db: AsyncSession = Depends(get_db)):
    return await result(db, CreditService(db).claim_notice())


@router.post("/internal/credit/notices/{notice_id}/ack")
async def ack_notice(notice_id: int, data: CreditNoticeAck, db: AsyncSession = Depends(get_db)):
    return await result(db, CreditService(db).ack_notice(notice_id, data))
