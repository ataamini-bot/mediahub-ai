from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.models.payment import PaymentStatus
from app.schemas.admin import (
    AdminPaymentCardCreate,
    AdminPaymentCardResponse,
    AdminPaymentCardUpdate,
    AdminPaymentListItem,
    AdminPaymentPageResponse,
    AdminPaymentSummaryResponse,
    AdminUsdtDestinationCreate,
    AdminUsdtDestinationResponse,
    AdminUsdtDestinationUpdate,
)
from app.services.admin_access import (
    AdminAccessDenied,
    AdminAccessService,
    AdminContext,
    PermissionCode,
)
from app.services.payment_management import (
    AdminPaymentRecord,
    PaymentDestinationConflict,
    PaymentDestinationNotFound,
    PaymentDestinationValidation,
    PaymentManagementService,
)


router = APIRouter(
    prefix="/admin",
    tags=["admin-finance"],
    dependencies=[Depends(require_internal_api_key)],
)


async def require_actor(
    db: AsyncSession,
    actor_telegram_id: int,
    permission: str,
) -> AdminContext:
    context = await AdminAccessService(db).require_permission(
        actor_telegram_id,
        permission,
    )
    if context.user_id is None:
        raise AdminAccessDenied("Administrator user is not registered")
    return context


def serialize_payment(record: AdminPaymentRecord) -> AdminPaymentListItem:
    payment = record.payment
    user = record.user
    return AdminPaymentListItem(
        id=payment.id,
        status=payment.status,
        amount=payment.amount,
        payment_method=payment.payment_method,
        plan_name_snapshot=payment.plan_name_snapshot,
        duration_days=payment.duration_days,
        receipt_file_id=payment.receipt_file_id,
        receipt_file_type=payment.receipt_file_type,
        receipt_mime_type=payment.receipt_mime_type,
        payment_destination_snapshot=payment.payment_destination_snapshot,
        reviewed_by_telegram_id=payment.reviewed_by_telegram_id,
        reviewed_at=payment.reviewed_at,
        rejection_reason=payment.rejection_reason,
        created_at=payment.created_at,
        user_telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


@router.get(
    "/payments/summary",
    response_model=AdminPaymentSummaryResponse,
)
async def payment_summary(
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENTS_VIEW,
        )
        return await PaymentManagementService(db).summary()
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get(
    "/payments",
    response_model=AdminPaymentPageResponse,
)
async def list_payments(
    actor_telegram_id: int = Query(gt=0),
    status: PaymentStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
) -> AdminPaymentPageResponse:
    try:
        await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENTS_VIEW,
        )
        result = await PaymentManagementService(db).list_payments(
            status=status,
            page=page,
            page_size=page_size,
        )
        return AdminPaymentPageResponse(
            items=[serialize_payment(item) for item in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
        )
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get(
    "/payments/{payment_id}",
    response_model=AdminPaymentListItem,
)
async def get_payment(
    payment_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> AdminPaymentListItem:
    try:
        await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENTS_VIEW,
        )
        record = await PaymentManagementService(db).get_payment(payment_id)
        return serialize_payment(record)
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/payment-cards",
    response_model=list[AdminPaymentCardResponse],
)
async def list_payment_cards(
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> list:
    try:
        await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        return await PaymentManagementService(db).list_cards()
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get(
    "/payment-cards/{card_id}",
    response_model=AdminPaymentCardResponse,
)
async def get_payment_card(
    card_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        return await PaymentManagementService(db).get_card(card_id)
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/payment-cards",
    response_model=AdminPaymentCardResponse,
    status_code=201,
)
async def create_payment_card(
    data: AdminPaymentCardCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        context = await require_actor(
            db,
            data.actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        card = await PaymentManagementService(db).create_card(
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            label=data.label,
            card_number=data.card_number,
            card_holder=data.card_holder,
            bank_name=data.bank_name,
            sort_order=data.sort_order,
            is_active=data.is_active,
        )
        await db.commit()
        await db.refresh(card)
        return card
    except AdminAccessDenied as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentDestinationValidation as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/payment-cards/{card_id}",
    response_model=AdminPaymentCardResponse,
)
async def update_payment_card(
    card_id: int,
    data: AdminPaymentCardUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        context = await require_actor(
            db,
            data.actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        changes = data.model_dump(
            exclude={"actor_telegram_id"},
            exclude_unset=True,
        )
        card = await PaymentManagementService(db).update_card(
            card_id=card_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            changes=changes,
        )
        await db.commit()
        await db.refresh(card)
        return card
    except AdminAccessDenied as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PaymentDestinationConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentDestinationValidation as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/payment-cards/{card_id}", status_code=204)
async def delete_payment_card(
    card_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        context = await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        await PaymentManagementService(db).delete_card(
            card_id=card_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=actor_telegram_id,
        )
        await db.commit()
        return Response(status_code=204)
    except AdminAccessDenied as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/usdt-destinations",
    response_model=list[AdminUsdtDestinationResponse],
)
async def list_usdt_destinations(
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> list:
    try:
        await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        return await PaymentManagementService(db).list_usdt_destinations()
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get(
    "/usdt-destinations/{destination_id}",
    response_model=AdminUsdtDestinationResponse,
)
async def get_usdt_destination(
    destination_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
):
    try:
        await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        return await PaymentManagementService(db).get_usdt_destination(
            destination_id
        )
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/usdt-destinations",
    response_model=AdminUsdtDestinationResponse,
    status_code=201,
)
async def create_usdt_destination(
    data: AdminUsdtDestinationCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        context = await require_actor(
            db,
            data.actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        payload = data.model_dump(exclude={"actor_telegram_id"})
        destination = await PaymentManagementService(
            db
        ).create_usdt_destination(
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            data=payload,
        )
        await db.commit()
        await db.refresh(destination)
        return destination
    except AdminAccessDenied as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/usdt-destinations/{destination_id}",
    response_model=AdminUsdtDestinationResponse,
)
async def update_usdt_destination(
    destination_id: int,
    data: AdminUsdtDestinationUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        context = await require_actor(
            db,
            data.actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        changes = data.model_dump(
            exclude={"actor_telegram_id"},
            exclude_unset=True,
        )
        destination = await PaymentManagementService(
            db
        ).update_usdt_destination(
            destination_id=destination_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=data.actor_telegram_id,
            changes=changes,
        )
        await db.commit()
        await db.refresh(destination)
        return destination
    except AdminAccessDenied as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PaymentDestinationConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/usdt-destinations/{destination_id}", status_code=204)
async def delete_usdt_destination(
    destination_id: int,
    actor_telegram_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        context = await require_actor(
            db,
            actor_telegram_id,
            PermissionCode.PAYMENT_DESTINATIONS_MANAGE,
        )
        await PaymentManagementService(db).delete_usdt_destination(
            destination_id=destination_id,
            actor_user_id=int(context.user_id),
            actor_telegram_id=actor_telegram_id,
        )
        await db.commit()
        return Response(status_code=204)
    except AdminAccessDenied as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentDestinationNotFound as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
