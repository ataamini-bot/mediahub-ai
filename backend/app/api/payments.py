from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from app.services.payment import (
    DuplicateReceipt,
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
    )


@router.get(
    "/configuration",
    response_model=PaymentConfigurationResponse,
)
async def payment_configuration(
    select_destination: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await get_payment_configuration(
            db,
            select_destination=select_destination,
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
