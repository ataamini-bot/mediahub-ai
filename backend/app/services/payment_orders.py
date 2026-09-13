"""One durable open checkout per customer; rotation occurs once per order."""
from uuid import UUID

from sqlalchemy import select

from app.core.language import effective_language
from app.models.payment import Payment, PaymentStatus
from app.models.payment_order import PaymentOrder
from app.models.user import User, UserStatus
from app.schemas.payment import PaymentOfferResponse, PaymentOrderCreate
from app.services.audit import AuditService
from app.services.managed_settings import ensure_public_operation, get_receipt_max_size_mb
from app.services.payment_offers import get_payment_offer
from app.services.payment_management import (
    PaymentManagementService, PaymentDestinationValidation,
    payment_card_snapshot, legacy_payment_card_snapshot,
)


class PaymentOrderError(RuntimeError):
    def __init__(self, code, *, status_code=409, **details):
        super().__init__(code)
        self.status_code = status_code
        self.detail = {"code": code, **details}


class PaymentOrderService:
    def __init__(self, session):
        self.session = session

    async def user(self, telegram_id, *, lock=False):
        query = select(User).where(User.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update()
        user = (await self.session.execute(query)).scalar_one_or_none()
        if user is None:
            raise PaymentOrderError("payment_order_not_found", status_code=404)
        if user.status != UserStatus.ACTIVE:
            raise PermissionError("User account is not active")
        return user

    @staticmethod
    def require_currency(user, currency):
        language = effective_language(preferred_language=user.preferred_language,
                                      telegram_language_code=user.language_code)
        if currency != ("USDT" if language == "en" else "IRT"):
            raise PaymentOrderError("payment_order_language", status_code=403)

    async def get(self, order_id: UUID, user_id, *, lock=False):
        query = select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.user_id == user_id).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        order = (await self.session.execute(query)).scalar_one_or_none()
        if order is None:
            raise PaymentOrderError("payment_order_not_found", status_code=404)
        return order

    async def current(self, telegram_id):
        user = await self.user(telegram_id)
        return (await self.session.execute(select(PaymentOrder).where(
            PaymentOrder.user_id == user.id, PaymentOrder.status == "open"
        ))).scalar_one_or_none()

    async def start(self, data: PaymentOrderCreate):
        user = await self.user(data.telegram_id, lock=True)
        self.require_currency(user, data.currency)
        pending = (await self.session.execute(select(Payment.id).where(
            Payment.user_id == user.id, Payment.status == PaymentStatus.PENDING
        ).limit(1))).scalar_one_or_none()
        if pending is not None:
            raise PaymentOrderError("pending_payment_exists", payment_id=pending)
        existing = (await self.session.execute(select(PaymentOrder).where(
            PaymentOrder.user_id == user.id, PaymentOrder.status == "open"
        ).with_for_update())).scalar_one_or_none()
        if existing is not None:
            if (existing.currency == data.currency
                    and existing.offer_snapshot["code"] == data.offer_code
                    and (data.currency == "IRT" or existing.destination_snapshot["id"] == data.usdt_destination_id)):
                # A retry must not consult a changed catalog or rotate again.
                await self.session.commit()
                return existing
            raise PaymentOrderError("open_payment_order_exists", order_id=str(existing.id))

        await ensure_public_operation(self.session, "payments")
        offer = await get_payment_offer(self.session, data.offer_code, currency=data.currency)
        management = PaymentManagementService(self.session)
        card_id = None
        if data.currency == "USDT":
            destination = await management.usdt_destination_for_submission(data.usdt_destination_id)
        else:
            card = await management.select_card()
            if card is not None:
                destination = payment_card_snapshot(card)
                card_id = card.id
            elif await management.has_database_cards():
                raise PaymentDestinationValidation("No active database payment card is configured")
            else:
                destination = legacy_payment_card_snapshot()
        language = "en" if data.currency == "USDT" else "fa"
        snapshot = PaymentOfferResponse(
            code=offer.code, label=offer.label, duration_days=offer.duration_days,
            price=offer.price, currency=data.currency, **offer.limits_snapshot(),
            description=offer.localized_description(language),
            description_fa=offer.description, description_en=offer.description_en,
        ).model_dump(mode="json")
        order = PaymentOrder(
            user_id=user.id, plan_id=offer.plan_id, payment_card_id=card_id,
            currency=data.currency, offer_snapshot=snapshot, destination_snapshot=destination,
            receipt_rules={"max_size_mb": await get_receipt_max_size_mb(self.session),
                           "allowed_types": ["photo", "image/jpeg", "image/png", "image/webp", "application/pdf"]},
        )
        self.session.add(order)
        await self.session.flush()
        AuditService(self.session).record(action="payment_order.created", actor_user_id=user.id,
            actor_telegram_id=user.telegram_id, target_type="payment_order", target_id=str(order.id),
            details={"currency": data.currency, "plan_id": offer.plan_id})
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def cancel(self, order_id, telegram_id):
        user = await self.user(telegram_id, lock=True)
        order = await self.get(order_id, user.id, lock=True)
        if order.status == "submitted":
            raise PaymentOrderError("payment_order_submitted")
        if order.status == "open":
            order.status = "cancelled"
            AuditService(self.session).record(action="payment_order.cancelled", actor_user_id=user.id,
                actor_telegram_id=user.telegram_id, target_type="payment_order", target_id=str(order.id))
        await self.session.commit()
        return order

    async def payload(self, order):
        if order is None:
            return None
        payment_id = (await self.session.execute(select(Payment.id).where(Payment.order_id == order.id))).scalar_one_or_none()
        return {"id": order.id, "status": order.status, "currency": order.currency,
                "offer": order.offer_snapshot, "destination": order.destination_snapshot,
                "receipt": order.receipt_rules, "created_at": order.created_at, "payment_id": payment_id}
