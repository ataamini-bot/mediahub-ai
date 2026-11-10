from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations import actor
from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.schemas.coupon import CouponChange, CouponQuote
from app.services.admin_access import AdminAccessDenied
from app.services.coupons import CouponError, CouponService
from app.services.managed_settings import PublicOperationDisabled
from app.services.payment_orders import PaymentOrderError
from app.services.payment_offers import PaymentConfigurationError

router = APIRouter(tags=["coupons"], dependencies=[Depends(require_internal_api_key)])


async def result(db, operation):
    try:
        value = await operation
        await db.commit()
        return value
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, {"code": "coupon_conflict"}) from exc
    except PaymentConfigurationError as exc:
        await db.rollback()
        raise HTTPException(409, {"code": "coupon_offer_unavailable"}) from exc
    except (CouponError, PaymentOrderError, AdminAccessDenied, LookupError, PermissionError, PublicOperationDisabled) as exc:
        await db.rollback()
        if isinstance(exc, PaymentOrderError):
            raise HTTPException(exc.status_code, exc.detail) from exc
        if isinstance(exc, PublicOperationDisabled):
            raise HTTPException(503, exc.detail()) from exc
        status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, LookupError) else 409
        raise HTTPException(status, {"code": exc.code if isinstance(exc, CouponError) else str(exc)}) from exc


@router.post("/payments/coupons/quote")
async def quote(data: CouponQuote, db: AsyncSession = Depends(get_db)):
    return await result(db, CouponService(db).quote(data))


@router.get("/admin/coupons")
async def listing(actor_telegram_id: int = Query(gt=0), page: int = Query(default=1, ge=1), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, "coupons.manage")
    return await result(db, CouponService(db).listing(page))


@router.get("/admin/coupons/options")
async def options(actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, "coupons.manage")
    return await result(db, CouponService(db).options())


@router.get("/admin/coupons/{coupon_id}")
async def detail(coupon_id: UUID, actor_telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    await actor(db, actor_telegram_id, "coupons.manage")
    return await result(db, CouponService(db).detail(coupon_id))


@router.post("/admin/coupons")
async def create(data: CouponChange, db: AsyncSession = Depends(get_db)):
    return await result(db, CouponService(db).change(data))


@router.put("/admin/coupons/{coupon_id}")
async def change(coupon_id: UUID, data: CouponChange, db: AsyncSession = Depends(get_db)):
    return await result(db, CouponService(db).change(data, coupon_id))
