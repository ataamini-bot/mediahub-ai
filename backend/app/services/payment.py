import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.language import effective_language
from app.models.payment import Payment, PaymentStatus
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.schemas.payment import PaymentCreate
from app.services.admin_access import AdminAccessService, PermissionCode
from app.services.payment_offers import get_payment_offer
from app.services.managed_settings import (
    ensure_public_operation,
    get_managed_setting,
    get_receipt_max_size_mb,
)
from app.services.payment_management import PaymentManagementService
from app.services.download_access import quota_day_start_utc
from app.services.audit import AuditService


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


class DuplicateTxID(PaymentConflict):
    pass


class InvalidReceipt(ValueError):
    pass


@dataclass(slots=True)
class PaymentActionResult:
    payment: Payment
    user: User
    subscription: Subscription | None = None
    already_reviewed: bool = False


def add_duration_days(value: datetime, days: int) -> datetime:
    if days <= 0:
        raise ValueError("Subscription duration must be positive")

    return value + timedelta(days=days)


def combine_daily_download_limits(
    current_limit: int | None,
    purchased_limit: int | None,
) -> int | None:
    """Stack finite renewal quotas; ``None`` keeps unlimited entitlement."""
    if current_limit is None or purchased_limit is None:
        return None
    return current_limit + purchased_limit


def payment_daily_download_limit(payment: Payment, plan: Plan) -> int | None:
    snapshot = (
        payment.plan_limits_snapshot
        if isinstance(payment.plan_limits_snapshot, dict)
        else {}
    )
    if "daily_download_limit" not in snapshot:
        return plan.daily_download_limit
    value = snapshot.get("daily_download_limit")
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return plan.daily_download_limit
    return parsed if parsed > 0 else plan.daily_download_limit


def canonical_network(value):
    code = str(value or "").strip().upper()
    aliases = {"TRC20": "TRON", "ERC20": "ETHEREUM", "ETH": "ETHEREUM",
               "BEP20": "BSC", "BEP-20": "BSC", "SOL": "SOLANA"}
    return aliases.get(code, code)


def normalize_transaction_id(value):
    value = str(value or "").strip()
    if re.fullmatch(r"(?:0[xX])?[a-fA-F0-9]{64}", value):
        return value[-64:].lower()
    # Base58 ids (for example Solana) are case-sensitive.
    return value


def _benefit(value: int | None) -> float:
    return float("inf") if value is None else float(value)


def classify_plan_change(current: Plan, purchased: Plan) -> str:
    """Classify custom plans without relying on names or fixed tiers."""
    current_values = (
        _benefit(current.daily_download_limit),
        _benefit(current.max_file_size_mb),
        _benefit(current.max_quality),
        float(current.max_concurrent_downloads),
        float(bool(current.priority_processing)),
        float(not bool(current.forced_join_required)),
    )
    purchased_values = (
        _benefit(purchased.daily_download_limit),
        _benefit(purchased.max_file_size_mb),
        _benefit(purchased.max_quality),
        float(purchased.max_concurrent_downloads),
        float(bool(purchased.priority_processing)),
        float(not bool(purchased.forced_join_required)),
    )
    if all(new >= old for old, new in zip(current_values, purchased_values)) and any(
        new > old for old, new in zip(current_values, purchased_values)
    ):
        return "upgrade"
    if all(new <= old for old, new in zip(current_values, purchased_values)):
        return "downgrade"
    return "switch"


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_admin(self, admin_telegram_id: int) -> None:
        await AdminAccessService(self.session).require_permission(
            admin_telegram_id,
            PermissionCode.PAYMENTS_REVIEW,
        )

    @staticmethod
    def validate_receipt(
        data: PaymentCreate,
        max_size_mb: int | None = None,
    ) -> None:
        configured_maximum = (
            settings.payment_receipt_max_size_mb
            if max_size_mb is None
            else max_size_mb
        )
        max_size_bytes = configured_maximum * 1024 * 1024

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
        await ensure_public_operation(self.session, "payments")
        self.validate_receipt(
            data,
            await get_receipt_max_size_mb(self.session),
        )
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

        language = effective_language(
            preferred_language=user.preferred_language,
            telegram_language_code=user.language_code,
        )
        expected_currency = "USDT" if language == "en" else "IRT"
        if data.currency != expected_currency:
            raise PermissionError(
                f"{data.currency} payments are not available in the active language"
            )

        offer = await get_payment_offer(
            self.session,
            data.offer_code,
            currency=data.currency,
        )
        management = PaymentManagementService(self.session)
        if data.currency == "USDT":
            payment_card_id = None
            destination_snapshot = await management.usdt_destination_for_submission(
                data.usdt_destination_id
            )
            payment_method = "usdt"
            network_code = canonical_network(destination_snapshot.get("network_code"))
            txid = str(data.txid or "").strip()
            txid_normalized = normalize_transaction_id(txid)
        else:
            payment_card_id, destination_snapshot = (
                await management.payment_card_for_submission(data.payment_card_id)
            )
            payment_method = "card"
            network_code = None
            txid = None
            txid_normalized = None

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

        if txid_normalized is not None:
            result = await self.session.execute(
                select(Payment.id)
                .where(
                    Payment.usdt_network_code == network_code,
                    Payment.txid_normalized == txid_normalized,
                )
                .limit(1)
            )
            if result.scalar_one_or_none() is not None:
                raise DuplicateTxID(
                    "This TxID was already submitted for the selected network"
                )

        payment = Payment(
            user_id=user.id,
            plan_id=offer.plan_id,
            amount=offer.price,
            offer_code=offer.code,
            duration_months=None,
            duration_days=offer.duration_days,
            plan_name_snapshot=offer.label,
            plan_limits_snapshot=offer.limits_snapshot(),
            status=PaymentStatus.PENDING,
            receipt_file_id=data.receipt_file_id,
            receipt_file_unique_id=data.receipt_file_unique_id,
            receipt_file_type=data.receipt_file_type,
            receipt_file_size=data.receipt_file_size,
            receipt_mime_type=data.receipt_mime_type,
            receipt_file_name=data.receipt_file_name,
            user_receipt_message_id=data.user_receipt_message_id,
            payment_method=payment_method,
            payment_card_id=payment_card_id,
            payment_destination_snapshot=destination_snapshot,
            txid=txid,
            txid_normalized=txid_normalized,
            usdt_network_code=network_code,
        )
        self.session.add(payment)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if "uq_payments_usdt_network_txid" in str(exc.orig):
                raise DuplicateTxID(
                    "This TxID was already submitted for the selected network"
                ) from exc
            if "uq_payments_receipt_file_unique_id" in str(exc.orig):
                raise DuplicateReceipt("This Telegram receipt file was already submitted") from exc
            raise

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

        # Delivery is independent of financial review. Keep the payment and
        # its duplicate protection pending in the finance panel for recovery.
        # A Telegram outage must never reject money the customer already sent.

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
        plan_result = await self.session.execute(
            select(Plan).where(Plan.id == payment.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            raise RuntimeError("Payment plan not found")

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
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.user_id == user.id,
                Subscription.status.in_(
                    (SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED)
                ),
                Subscription.started_at <= now,
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc(), Subscription.id.desc())
            .limit(1)
            .with_for_update()
        )
        current_row = result.first()
        current_subscription = current_row[0] if current_row else None
        current_plan = current_row[1] if current_row else None

        if current_subscription is None:
            purchased_daily_limit = payment_daily_download_limit(payment, plan)
            future_end = (await self.session.execute(
                select(func.max(Subscription.expires_at)).where(
                    Subscription.user_id == user.id,
                    Subscription.status == SubscriptionStatus.SCHEDULED,
                    Subscription.started_at > now,
                    Subscription.expires_at > now,
                )
            )).scalar_one()
            starts_scheduled = future_end is not None
            started_at = future_end if starts_scheduled else now
            subscription = Subscription(
                user_id=user.id,
                plan_id=payment.plan_id,
                status=(SubscriptionStatus.SCHEDULED if starts_scheduled else SubscriptionStatus.ACTIVE),
                started_at=started_at,
                expires_at=add_duration_days(
                    started_at,
                    payment.duration_days,
                ),
                daily_download_limit=purchased_daily_limit,
                auto_renew=False,
            )
            self.session.add(subscription)
            await self.session.flush()
            payment.subscription_change_type = "scheduled" if starts_scheduled else "new"
        elif current_subscription.plan_id == payment.plan_id:
            subscription = current_subscription
            subscription.status = SubscriptionStatus.ACTIVE
            await self._shift_future_subscriptions(user.id, now, timedelta(days=payment.duration_days))
            subscription.expires_at = add_duration_days(subscription.expires_at, payment.duration_days)
            subscription.daily_download_limit = combine_daily_download_limits(
                subscription.daily_download_limit,
                payment_daily_download_limit(payment, plan),
            )
            payment.subscription_change_type = "renewal"
        else:
            change_type = classify_plan_change(current_plan, plan)
            if change_type == "upgrade":
                # Preserve the exact remaining time, including partial days.
                remaining_time = max(timedelta(0), current_subscription.expires_at - now)
                await self._shift_future_subscriptions(user.id, now, timedelta(days=payment.duration_days))
                current_subscription.status = SubscriptionStatus.CANCELLED
                subscription = Subscription(
                    user_id=user.id,
                    plan_id=payment.plan_id,
                    status=SubscriptionStatus.ACTIVE,
                    started_at=now,
                    expires_at=add_duration_days(now, payment.duration_days) + remaining_time,
                    daily_download_limit=payment_daily_download_limit(payment, plan),
                    auto_renew=False,
                )
                self.session.add(subscription)
                await self.session.flush()
                payment.subscription_change_type = "upgrade"
            else:
                # Downgrades and mixed plan switches are scheduled so already
                # paid entitlements are never removed early.
                latest_end = (await self.session.execute(
                    select(func.max(Subscription.expires_at)).where(
                        Subscription.user_id == user.id,
                        Subscription.status.in_((SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED)),
                        Subscription.expires_at > now,
                    )
                )).scalar_one()
                start_at = latest_end or current_subscription.expires_at
                subscription = Subscription(
                    user_id=user.id,
                    plan_id=payment.plan_id,
                    status=SubscriptionStatus.SCHEDULED,
                    started_at=start_at,
                    expires_at=add_duration_days(start_at, payment.duration_days),
                    daily_download_limit=payment_daily_download_limit(payment, plan),
                    auto_renew=False,
                )
                self.session.add(subscription)
                await self.session.flush()
                payment.subscription_change_type = change_type

        payment.status = PaymentStatus.APPROVED
        payment.reviewed_by_telegram_id = admin_telegram_id
        payment.reviewed_at = now
        payment.rejection_reason = None
        payment.subscription_id = subscription.id

        AuditService(self.session).record(action="payment.approved", actor_telegram_id=admin_telegram_id,
            target_type="payment", target_id=payment.id,
            details={"subscription_id": subscription.id, "change_type": payment.subscription_change_type})
        await self.session.commit()
        await self.session.refresh(payment)
        await self.session.refresh(subscription)

        return PaymentActionResult(
            payment=payment,
            user=user,
            subscription=subscription,
        )

    async def _shift_future_subscriptions(self, user_id, now, delta):
        rows = (await self.session.execute(select(Subscription).where(
            Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.SCHEDULED,
            Subscription.started_at > now,
        ).with_for_update())).scalars()
        for future in rows:
            future.started_at += delta
            future.expires_at += delta

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
        AuditService(self.session).record(action="payment.rejected", actor_telegram_id=admin_telegram_id,
            target_type="payment", target_id=payment.id, details={"reason": reason.strip()})

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
                Subscription.status.in_(
                    (SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED)
                ),
                Subscription.started_at <= now,
                Subscription.expires_at > now,
            )
            .order_by(
                Subscription.expires_at.desc(),
                Subscription.id.desc(),
            )
            .limit(1)
        )
        return result.first()

    async def get_subscription_details(self, *, telegram_id: int) -> dict | None:
        now = datetime.now(timezone.utc)
        queued_rows = (await self.session.execute(
            select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id)
            .join(User, User.id == Subscription.user_id).where(
                User.telegram_id == telegram_id, Subscription.status == SubscriptionStatus.SCHEDULED,
                Subscription.started_at > now,
            ).order_by(Subscription.started_at, Subscription.id)
        )).all()
        scheduled = [{"id": item.id, "plan_name": catalog.name,
            "plan_name_en": catalog.name_en if catalog.name_en and not re.search(r"[\u0600-\u06ff]", catalog.name_en) else f"Plan {catalog.id}",
            "started_at": item.started_at, "expires_at": item.expires_at} for item, catalog in queued_rows]
        result = await self.session.execute(
            select(Subscription, Plan, User)
            .join(Plan, Plan.id == Subscription.plan_id)
            .join(User, User.id == Subscription.user_id)
            .where(
                User.telegram_id == telegram_id,
                Subscription.status.in_(
                    (SubscriptionStatus.ACTIVE, SubscriptionStatus.SCHEDULED)
                ),
                Subscription.started_at <= now,
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc(), Subscription.id.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return {"is_active": False, "scheduled": scheduled}
        subscription, plan, user = row
        count_result = await self.session.execute(
            select(func.count(DownloadJob.id)).where(
                DownloadJob.user_id == user.id,
                DownloadJob.status == DownloadJobStatus.COMPLETED,
            )
        )
        downloads_done = int(count_result.scalar() or 0)
        daily_limit = subscription.daily_download_limit
        timezone_name = await get_managed_setting(
            self.session,
            "quota.timezone",
        )
        day_start = quota_day_start_utc(timezone_name=timezone_name)
        reserved_statuses = (
            DownloadJobStatus.PENDING,
            DownloadJobStatus.PROCESSING,
            DownloadJobStatus.PAUSED,
        )
        daily_result = await self.session.execute(
            select(func.count(DownloadJob.id)).where(
                DownloadJob.user_id == user.id,
                or_(
                    DownloadJob.delivered_at >= day_start,
                    DownloadJob.status.in_(reserved_statuses),
                    and_(
                        DownloadJob.status == DownloadJobStatus.COMPLETED,
                        DownloadJob.delivered_at.is_(None),
                        DownloadJob.created_at >= day_start,
                    ),
                ),
            )
        )
        used_today = int(daily_result.scalar() or 0)
        return {
            "is_active": True,
            "scheduled": scheduled,
            "plan_slug": plan.slug,
            "plan_name": plan.name,
            "plan_name_en": plan.name_en if plan.name_en and not re.search(r"[\u0600-\u06ff]", plan.name_en) else f"Plan {plan.id}",
            "started_at": subscription.started_at,
            "expires_at": subscription.expires_at,
            "duration_days": plan.duration_days,
            "registered_at": user.created_at,
            "downloads_done": downloads_done,
            "daily_download_limit": daily_limit,
            "remaining_downloads": None if daily_limit is None else max(daily_limit - used_today, 0),
        }

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
