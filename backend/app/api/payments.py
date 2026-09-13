from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.schemas.payment import (
    CurrentSubscriptionResponse,
    PaymentActionResponse,
    PaymentAdminMessageUpdate,
    PaymentAdminReview,
    PaymentConfigurationResponse,
    PaymentCreate,
    PaymentReject,
    PaymentResponse,
    PaymentUserResponse,
    SubscriptionResponse,
    PaymentOrderCreate, PaymentOrderActor, PaymentOrderResponse,
)
from app.services.payment import (
    DuplicateReceipt,
    DuplicateTxID,
    InvalidReceipt,
    PaymentActionResult,
    PaymentConflict,
    PaymentNotFound,
    PaymentService,
    PendingPaymentExists,
)
from app.services.payment_offers import (
    PaymentConfigurationError,
    get_payment_configuration,
)
from app.services.managed_settings import PublicOperationDisabled
from app.services.payment_management import PaymentDestinationValidation
from app.services.payment_orders import PaymentOrderService, PaymentOrderError


router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(require_internal_api_key)],
)


def serialize_action(result: PaymentActionResult) -> PaymentActionResponse:
    return PaymentActionResponse(
        payment=PaymentResponse.model_validate(result.payment),
        user=PaymentUserResponse.model_validate(result.user),
        subscription=(
            SubscriptionResponse.model_validate(result.subscription)
            if result.subscription is not None
            else None
        ),
        already_reviewed=result.already_reviewed,
        already_submitted=result.already_submitted,
    )


async def order_response(db, operation):
    try:
        order = await operation
        return await PaymentOrderService(db).payload(order)
    except (PaymentOrderError, PaymentConfigurationError, PaymentDestinationValidation,
            LookupError, PermissionError, PublicOperationDisabled) as exc:
        await db.rollback()
        if isinstance(exc, PaymentOrderError):
            raise HTTPException(exc.status_code, exc.detail) from exc
        if isinstance(exc, PublicOperationDisabled):
            raise HTTPException(503, exc.detail()) from exc
        status = (403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, LookupError)
                  else 503 if isinstance(exc, PaymentConfigurationError) else 409)
        raise HTTPException(status, str(exc)) from exc


@router.post("/orders", response_model=PaymentOrderResponse)
async def start_order(data: PaymentOrderCreate, db: AsyncSession = Depends(get_db)):
    return await order_response(db, PaymentOrderService(db).start(data))


@router.get("/orders/current", response_model=PaymentOrderResponse | None)
async def current_order(telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    return await order_response(db, PaymentOrderService(db).current(telegram_id))


@router.get("/orders/{order_id}", response_model=PaymentOrderResponse)
async def get_order(order_id: UUID, telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    async def load():
        service = PaymentOrderService(db)
        user = await service.user(telegram_id)
        return await service.get(order_id, user.id)
    return await order_response(db, load())


@router.post("/orders/{order_id}/cancel", response_model=PaymentOrderResponse)
async def cancel_order(order_id: UUID, data: PaymentOrderActor, db: AsyncSession = Depends(get_db)):
    return await order_response(db, PaymentOrderService(db).cancel(order_id, data.telegram_id))


@router.get(
    "/configuration",
    response_model=PaymentConfigurationResponse,
)
async def payment_configuration(
    select_destination: bool = Query(default=True),
    language: str = Query(default="fa", pattern="^(fa|en)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await get_payment_configuration(
            db,
            select_destination=select_destination,
            language=language,
        )
        await db.commit()
        return result
    except PaymentConfigurationError as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PublicOperationDisabled as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=exc.detail()) from exc


@router.post("", response_model=PaymentActionResponse)
async def create_payment(
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
) -> PaymentActionResponse:
    service = PaymentService(db)

    try:
        return serialize_action(await service.create_payment(data))
    except PaymentOrderError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, exc.detail) from exc
    except PaymentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PendingPaymentExists as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pending_payment_exists",
                "payment_id": exc.payment_id,
            },
        ) from exc
    except DuplicateReceipt as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate_receipt", "message": str(exc)},
        ) from exc
    except DuplicateTxID as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate_txid", "message": str(exc)},
        ) from exc
    except InvalidReceipt as exc:
        status_code = 413 if "size limit" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PublicOperationDisabled as exc:
        raise HTTPException(status_code=503, detail=exc.detail()) from exc
    except PaymentDestinationValidation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/{payment_id}/admin-message",
    response_model=PaymentActionResponse,
)
async def set_payment_admin_message(
    payment_id: int,
    data: PaymentAdminMessageUpdate,
    db: AsyncSession = Depends(get_db),
) -> PaymentActionResponse:
    service = PaymentService(db)

    try:
        result = await service.set_admin_message(
            payment_id=payment_id,
            admin_chat_id=data.admin_chat_id,
            admin_message_id=data.admin_message_id,
            admin_message_thread_id=data.admin_message_thread_id,
        )
        return serialize_action(result)
    except PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PaymentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{payment_id}/delivery-failed",
    response_model=PaymentActionResponse,
)
async def mark_payment_delivery_failed(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
) -> PaymentActionResponse:
    service = PaymentService(db)

    try:
        return serialize_action(
            await service.mark_delivery_failed(payment_id=payment_id)
        )
    except PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{payment_id}/approve",
    response_model=PaymentActionResponse,
)
async def approve_payment(
    payment_id: int,
    data: PaymentAdminReview,
    db: AsyncSession = Depends(get_db),
) -> PaymentActionResponse:
    service = PaymentService(db)

    try:
        return serialize_action(
            await service.approve(
                payment_id=payment_id,
                admin_telegram_id=data.admin_telegram_id,
            )
        )
    except PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{payment_id}/reject",
    response_model=PaymentActionResponse,
)
async def reject_payment(
    payment_id: int,
    data: PaymentReject,
    db: AsyncSession = Depends(get_db),
) -> PaymentActionResponse:
    service = PaymentService(db)

    try:
        return serialize_action(
            await service.reject(
                payment_id=payment_id,
                admin_telegram_id=data.admin_telegram_id,
                reason=data.reason,
            )
        )
    except PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/subscription/{telegram_id}",
    response_model=CurrentSubscriptionResponse,
)
async def current_subscription(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
) -> CurrentSubscriptionResponse:
    service = PaymentService(db)
    result = await service.get_subscription_details(telegram_id=telegram_id)

    if result is None:
        return CurrentSubscriptionResponse(is_active=False)

    return CurrentSubscriptionResponse.model_validate(result)
