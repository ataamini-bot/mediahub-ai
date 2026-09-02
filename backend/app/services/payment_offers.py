from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.plan import Plan


class PaymentConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaymentOffer:
    code: str
    label: str
    plan_id: int
    duration_days: int
    price: Decimal
    daily_download_limit: int | None
    max_file_size_mb: int | None
    max_quality: int | None
    max_concurrent_downloads: int
    priority_processing: bool
    forced_join_required: bool

    @classmethod
    def from_plan(cls, plan: Plan) -> "PaymentOffer":
        return cls(
            code=plan.slug,
            label=plan.name,
            plan_id=plan.id,
            duration_days=plan.duration_days,
            price=plan.price,
            daily_download_limit=plan.daily_download_limit,
            max_file_size_mb=plan.max_file_size_mb,
            max_quality=plan.max_quality,
            max_concurrent_downloads=plan.max_concurrent_downloads,
            priority_processing=plan.priority_processing,
            forced_join_required=plan.forced_join_required,
        )

    def limits_snapshot(self) -> dict:
        return {
            "daily_download_limit": self.daily_download_limit,
            "max_file_size_mb": self.max_file_size_mb,
            "max_quality": self.max_quality,
            "max_concurrent_downloads": self.max_concurrent_downloads,
            "priority_processing": self.priority_processing,
            "forced_join_required": self.forced_join_required,
        }


async def get_payment_offers(
    session: AsyncSession,
) -> tuple[PaymentOffer, ...]:
    result = await session.execute(
        select(Plan)
        .where(
            Plan.is_system.is_(False),
            Plan.is_active.is_(True),
            Plan.deleted_at.is_(None),
            Plan.duration_days > 0,
            Plan.price > 0,
        )
        .order_by(Plan.sort_order, Plan.id)
    )
    offers = tuple(PaymentOffer.from_plan(plan) for plan in result.scalars())

    if not offers:
        raise PaymentConfigurationError(
            "No active paid subscription plan is configured"
        )

    return offers


async def get_payment_offer(
    session: AsyncSession,
    code: str,
) -> PaymentOffer:
    normalized_code = str(code or "").strip().lower()
    result = await session.execute(
        select(Plan).where(
            Plan.slug == normalized_code,
            Plan.is_system.is_(False),
            Plan.is_active.is_(True),
            Plan.deleted_at.is_(None),
            Plan.duration_days > 0,
            Plan.price > 0,
        )
    )
    plan = result.scalar_one_or_none()

    if plan is None:
        raise LookupError("Subscription plan not found")

    return PaymentOffer.from_plan(plan)


def validate_payment_destination() -> None:
    missing_fields = []

    if not settings.payment_card_number.strip():
        missing_fields.append("PAYMENT_CARD_NUMBER")

    if not settings.payment_card_holder.strip():
        missing_fields.append("PAYMENT_CARD_HOLDER")

    if missing_fields:
        raise PaymentConfigurationError(
            "Payment destination is not configured: "
            + ", ".join(missing_fields)
        )


async def get_payment_configuration(session: AsyncSession) -> dict:
    validate_payment_destination()
    offers = await get_payment_offers(session)

    return {
        "offers": [
            {
                "code": offer.code,
                "label": offer.label,
                "duration_days": offer.duration_days,
                "price": offer.price,
                "currency": "IRT",
                "daily_download_limit": offer.daily_download_limit,
                "max_file_size_mb": offer.max_file_size_mb,
                "max_quality": offer.max_quality,
                "max_concurrent_downloads": offer.max_concurrent_downloads,
                "priority_processing": offer.priority_processing,
                "forced_join_required": offer.forced_join_required,
            }
            for offer in offers
        ],
        "destination": {
            "card_number": settings.payment_card_number.strip(),
            "card_holder": settings.payment_card_holder.strip(),
            "bank_name": settings.payment_bank_name.strip() or None,
        },
        "receipt": {
            "max_size_mb": settings.payment_receipt_max_size_mb,
            "allowed_types": [
                "photo",
                "image/jpeg",
                "image/png",
                "image/webp",
                "application/pdf",
            ],
        },
    }
