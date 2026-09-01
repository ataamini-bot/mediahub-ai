import calendar
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.payment import Payment, PaymentStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.schemas.payment import PaymentCreate
from app.services.admin_access import AdminAccessService, PermissionCode
from app.services.payment_offers import get_payment_offer


ALLOWED_RECEIPT_DOCUMENT_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}


class PaymentNotFound(LookupError):
    pass


class PaymentConflict(RuntimeError):
    pass


class PendingPaymentExists(PaymentConflict):
    def __init__(self, payment_id: int):
        self.payment_id = payment_id
        super().__init__("A pending payment already exists for this user")


class DuplicateReceipt(PaymentConflict):
    pass


class InvalidReceipt(ValueError):
    pass


@dataclass(slots=True)
class PaymentActionResult:
    payment: Payment
    user: User
    subscription: Subscription | None = None
    already_reviewed: bool = False


def add_calendar_months(value: datetime, months: int) -> datetime:
    if months not in {1, 3, 6, 12}:
        raise ValueError("Unsupported subscription duration")

    month_index = value.month - 1 + months
    target_year = value.year + month_index // 12
    target_month = month_index % 12 + 1
    target_day = min(
        value.day,
        calendar.monthrange(target_year, target_month)[1],
    )

    return value.replace(
        year=target_year,
        month=target_month,
        day=target_day,
    )


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_admin(self, admin_telegram_id: int) -> None:
        await AdminAccessService(self.session).require_permission(
            admin_telegram_id,
            PermissionCode.PAYMENTS_REVIEW,
        )

    @staticmethod
    def validate_receipt(data: PaymentCreate) -> None:
        max_size_bytes = settings.payment_receipt_max_size_mb * 1024 * 1024

        if (
            data.receipt_file_size is not None
            and data.receipt_file_size > max_size_bytes
        ):
            raise InvalidReceipt(
                "Receipt file exceeds the configured size limit"
            )

        if data.receipt_file_type == "document":
            mime_type = (data.receipt_mime_type or "").strip().lower()

            if mime_type not in ALLOWED_RECEIPT_DOCUMENT_MIME_TYPES:
                raise InvalidReceipt(
                    "Receipt document must be JPEG, PNG, WEBP, or PDF"
                )

    async def create_payment(
        self,
        data: PaymentCreate,
    ) -> PaymentActionResult:
        self.validate_receipt(data)
        offer = get_payment_offer(data.offer_code)

        result = await self.session.execute(
            select(User)
            .where(User.telegram_id == data.telegram_id)
            .with_for_update()
        )
        user = result.scalar_one_or_none()

        if user is None:
            raise LookupError("Telegram user is not registered")

        if user.status != UserStatus.ACTIVE:
            raise PermissionError("User account is not active")

        result = await self.session.execute(
            select(Payment)
            .where(
                Payment.user_id == user.id,
                Payment.status == PaymentStatus.PENDING,
            )
            .order_by(Payment.id.desc())
            .limit(1)
        )
        pending_payment = result.scalar_one_or_none()

        if pending_payment is not None:
            raise PendingPaymentExists(pending_payment.id)

        if data.receipt_file_unique_id:
            result = await self.session.execute(
                select(Payment.id)
                .where(
                    Payment.receipt_file_unique_id
                    == data.receipt_file_unique_id
                )
                .limit(1)
            )

            if result.scalar_one_or_none() is not None:
                raise DuplicateReceipt(
                    "This Telegram receipt file was already submitted"
                )

        result = await self.session.execute(
            select(Plan).where(
                Plan.slug == "premium",
                Plan.is_active.is_(True),
            )
        )
        plan = result.scalar_one_or_none()

        if plan is None:
            raise RuntimeError("Active premium plan is not configured")

        payment = Payment(
            user_id=user.id,
            plan_id=plan.id,
            amount=offer.price,
            offer_code=offer.code,
            duration_months=offer.duration_months,
            status=PaymentStatus.PENDING,
            receipt_file_id=data.receipt_file_id,
            receipt_file_unique_id=data.receipt_file_unique_id,
            receipt_file_type=data.receipt_file_type,
            receipt_file_size=data.receipt_file_size,
            receipt_mime_type=data.receipt_mime_type,
            receipt_file_name=data.receipt_file_name,
            user_receipt_message_id=data.user_receipt_message_id,
        )
        self.session.add(payment)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateReceipt(
                "This Telegram receipt file was already submitted"
            ) from exc

        await self.session.refresh(payment)

        return PaymentActionResult(payment=payment, user=user)

    async def set_admin_message(
        self,
        *,
        payment_id: int,
        admin_chat_id: int,
        admin_message_id: int,
        admin_message_thread_id: int | None,
    ) -> PaymentActionResult:
        payment = await self._get_payment_for_update(payment_id)
        user = await self._get_user(payment.user_id)

        if payment.status != PaymentStatus.PENDING:
            raise PaymentConflict("Only pending payments can be updated")

        payment.admin_chat_id = admin_chat_id
        payment.admin_message_id = admin_message_id
        payment.admin_message_thread_id = admin_message_thread_id
        await self.session.commit()
        await self.session.refresh(payment)

        return PaymentActionResult(payment=payment, user=user)

    async def mark_delivery_failed(
        self,
        *,
        payment_id: int,
    ) -> PaymentActionResult:
        payment = await self._get_payment_for_update(payment_id)
        user = await self._get_user(payment.user_id)

        if payment.status == PaymentStatus.PENDING:
            payment.status = PaymentStatus.REJECTED
            payment.rejection_reason = "admin_delivery_failed"
            payment.receipt_file_unique_id = None
            payment.reviewed_at = datetime.now(timezone.utc)
            await self.session.commit()
            await self.session.refresh(payment)

        return PaymentActionResult(
            payment=payment,
            user=user,
            already_reviewed=payment.status != PaymentStatus.PENDING,
        )

    async def approve(
        self,
        *,
        payment_id: int,
        admin_telegram_id: int,
    ) -> PaymentActionResult:
        await self.ensure_admin(admin_telegram_id)
        payment = await self._get_payment_for_update(payment_id)
        user = await self._get_user_for_update(payment.user_id)

        if payment.status == PaymentStatus.APPROVED:
            subscription = await self._get_linked_subscription(payment)
            return PaymentActionResult(
                payment=payment,
                user=user,
                subscription=subscription,
                already_reviewed=True,
            )

        if payment.status == PaymentStatus.REJECTED:
            raise PaymentConflict("Rejected payment cannot be approved")

        now = datetime.now(timezone.utc)

        await self.session.execute(
            update(Subscription)
            .where(
                Subscription.user_id == user.id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at <= now,
            )
            .values(status=SubscriptionStatus.EXPIRED)
        )

        result = await self.session.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user.id,
                Subscription.plan_id == payment.plan_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.started_at <= now,
                Subscription.expires_at > now,
            )
            .order_by(
                Subscription.expires_at.desc(),
                Subscription.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        subscription = result.scalar_one_or_none()

        if subscription is None:
            subscription = Subscription(
                user_id=user.id,
                plan_id=payment.plan_id,
                status=SubscriptionStatus.ACTIVE,
                started_at=now,
                expires_at=add_calendar_months(
                    now,
                    payment.duration_months,
                ),
                auto_renew=False,
            )
            self.session.add(subscription)
            await self.session.flush()
        else:
            subscription.expires_at = add_calendar_months(
                subscription.expires_at,
                payment.duration_months,
            )

        payment.status = PaymentStatus.APPROVED
        payment.reviewed_by_telegram_id = admin_telegram_id
        payment.reviewed_at = now
        payment.rejection_reason = None
        payment.subscription_id = subscription.id

        await self.session.commit()
        await self.session.refresh(payment)
        await self.session.refresh(subscription)

        return PaymentActionResult(
            payment=payment,
            user=user,
            subscription=subscription,
        )

    async def reject(
        self,
        *,
        payment_id: int,
        admin_telegram_id: int,
        reason: str,
    ) -> PaymentActionResult:
        await self.ensure_admin(admin_telegram_id)
        payment = await self._get_payment_for_update(payment_id)
        user = await self._get_user(payment.user_id)

        if payment.status == PaymentStatus.REJECTED:
            return PaymentActionResult(
                payment=payment,
                user=user,
                already_reviewed=True,
            )

        if payment.status == PaymentStatus.APPROVED:
            raise PaymentConflict("Approved payment cannot be rejected")

        payment.status = PaymentStatus.REJECTED
        payment.reviewed_by_telegram_id = admin_telegram_id
        payment.reviewed_at = datetime.now(timezone.utc)
        payment.rejection_reason = reason.strip()

        await self.session.commit()
        await self.session.refresh(payment)

        return PaymentActionResult(payment=payment, user=user)

    async def get_current_subscription(
        self,
        *,
        telegram_id: int,
    ) -> tuple[Subscription, Plan] | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .join(User, User.id == Subscription.user_id)
            .where(
                User.telegram_id == telegram_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.started_at <= now,
                Subscription.expires_at > now,
                Plan.is_active.is_(True),
            )
            .order_by(
                Subscription.expires_at.desc(),
                Subscription.id.desc(),
            )
            .limit(1)
        )
        return result.first()

    async def _get_payment_for_update(self, payment_id: int) -> Payment:
        result = await self.session.execute(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
        )
        payment = result.scalar_one_or_none()

        if payment is None:
            raise PaymentNotFound("Payment not found")

        return payment

    async def _get_user(self, user_id: int) -> User:
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            raise RuntimeError("Payment user not found")

        return user

    async def _get_user_for_update(self, user_id: int) -> User:
        result = await self.session.execute(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
        )
        user = result.scalar_one_or_none()

        if user is None:
            raise RuntimeError("Payment user not found")

        return user

    async def _get_linked_subscription(
        self,
        payment: Payment,
    ) -> Subscription | None:
        if payment.subscription_id is None:
            return None

        result = await self.session.execute(
            select(Subscription).where(
                Subscription.id == payment.subscription_id
            )
        )
        return result.scalar_one_or_none()
