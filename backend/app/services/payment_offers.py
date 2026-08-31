from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings


class PaymentConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaymentOffer:
    code: str
    label: str
    duration_months: int
    price: Decimal


def get_payment_offers() -> tuple[PaymentOffer, ...]:
    offers = (
        PaymentOffer(
            code="premium_1m",
            label="اشتراک ۱ ماهه",
            duration_months=1,
            price=settings.payment_price_1_month,
        ),
        PaymentOffer(
            code="premium_3m",
            label="اشتراک ۳ ماهه",
            duration_months=3,
            price=settings.payment_price_3_months,
        ),
        PaymentOffer(
            code="premium_6m",
            label="اشتراک ۶ ماهه",
            duration_months=6,
            price=settings.payment_price_6_months,
        ),
        PaymentOffer(
            code="premium_12m",
            label="اشتراک ۱۲ ماهه",
            duration_months=12,
            price=settings.payment_price_12_months,
        ),
    )

    invalid_codes = [
        offer.code
        for offer in offers
        if offer.price <= 0
    ]

    if invalid_codes:
        raise PaymentConfigurationError(
            "Payment prices are not configured for: "
            + ", ".join(invalid_codes)
        )

    return offers


def get_payment_offer(code: str) -> PaymentOffer:
    normalized_code = str(code or "").strip().lower()

    for offer in get_payment_offers():
        if offer.code == normalized_code:
            return offer

    raise LookupError("Subscription offer not found")


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


def get_payment_configuration() -> dict:
    validate_payment_destination()
    offers = get_payment_offers()

    return {
        "offers": [
            {
                "code": offer.code,
                "label": offer.label,
                "duration_months": offer.duration_months,
                "price": offer.price,
                "currency": "IRT",
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
