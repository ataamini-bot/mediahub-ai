from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan
from app.services.managed_settings import (
    ensure_public_operation,
    get_receipt_max_size_mb,
)
from app.services.payment_management import (
    PaymentDestinationValidation,
    PaymentManagementService,
    legacy_payment_card_snapshot,
    payment_card_snapshot,
)


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


async def get_payment_configuration(
    session: AsyncSession,
    *,
    select_destination: bool = True,
) -> dict:
    await ensure_public_operation(session, "payments")
    offers = await get_payment_offers(session)

    destination = None
    if select_destination:
        management = PaymentManagementService(session)
        card = await management.select_card()
        try:
            if card is not None:
                destination = payment_card_snapshot(card)
            elif await management.has_database_cards():
                raise PaymentDestinationValidation(
                    "No active database payment card is configured"
                )
            else:
                destination = legacy_payment_card_snapshot()
        except PaymentDestinationValidation as exc:
            raise PaymentConfigurationError(str(exc)) from exc

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
        "destination": destination,
        "receipt": {
            "max_size_mb": await get_receipt_max_size_mb(session),
            "allowed_types": [
                "photo",
                "image/jpeg",
                "image/png",
                "image/webp",
                "application/pdf",
            ],
        },
    }
