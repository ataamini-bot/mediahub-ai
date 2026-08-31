import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token")


from app.core.config import settings  # noqa: E402
from app.core.internal_auth import require_internal_api_key  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.payment import PaymentCreate  # noqa: E402
from app.services.payment import (  # noqa: E402
    InvalidReceipt,
    PaymentService,
    add_calendar_months,
)
from app.services.payment_offers import (  # noqa: E402
    PaymentConfigurationError,
    get_payment_offer,
    get_payment_offers,
)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (
            datetime(2025, 1, 31, 12, tzinfo=timezone.utc),
            1,
            datetime(2025, 2, 28, 12, tzinfo=timezone.utc),
        ),
        (
            datetime(2024, 1, 31, 12, tzinfo=timezone.utc),
            1,
            datetime(2024, 2, 29, 12, tzinfo=timezone.utc),
        ),
        (
            datetime(2024, 2, 29, 12, tzinfo=timezone.utc),
            12,
            datetime(2025, 2, 28, 12, tzinfo=timezone.utc),
        ),
        (
            datetime(2025, 8, 30, 12, tzinfo=timezone.utc),
            6,
            datetime(2026, 2, 28, 12, tzinfo=timezone.utc),
        ),
    ],
)
def test_add_calendar_months(start, months, expected):
    assert add_calendar_months(start, months) == expected


def test_add_calendar_months_rejects_unknown_duration():
    with pytest.raises(ValueError):
        add_calendar_months(datetime.now(timezone.utc), 2)


def test_payment_offers_have_exact_supported_durations(monkeypatch):
    monkeypatch.setattr(settings, "payment_price_1_month", Decimal("100"))
    monkeypatch.setattr(settings, "payment_price_3_months", Decimal("250"))
    monkeypatch.setattr(settings, "payment_price_6_months", Decimal("450"))
    monkeypatch.setattr(settings, "payment_price_12_months", Decimal("800"))

    offers = get_payment_offers()

    assert [offer.code for offer in offers] == [
        "premium_1m",
        "premium_3m",
        "premium_6m",
        "premium_12m",
    ]
    assert [offer.duration_months for offer in offers] == [1, 3, 6, 12]
    assert get_payment_offer(" PREMIUM_3M ").duration_months == 3


def test_payment_offers_reject_zero_price(monkeypatch):
    monkeypatch.setattr(settings, "payment_price_1_month", Decimal("100"))
    monkeypatch.setattr(settings, "payment_price_3_months", Decimal("250"))
    monkeypatch.setattr(settings, "payment_price_6_months", Decimal("450"))
    monkeypatch.setattr(settings, "payment_price_12_months", Decimal("0"))

    with pytest.raises(PaymentConfigurationError):
        get_payment_offers()


def test_receipt_validation_rejects_large_file(monkeypatch):
    monkeypatch.setattr(settings, "payment_receipt_max_size_mb", 1)
    data = PaymentCreate(
        telegram_id=123,
        offer_code="premium_1m",
        receipt_file_id="file-id",
        receipt_file_type="photo",
        receipt_file_size=1024 * 1024 + 1,
        user_receipt_message_id=1,
    )

    with pytest.raises(InvalidReceipt):
        PaymentService.validate_receipt(data)


def test_receipt_validation_rejects_unknown_document_type():
    data = PaymentCreate(
        telegram_id=123,
        offer_code="premium_1m",
        receipt_file_id="file-id",
        receipt_file_type="document",
        receipt_file_size=100,
        receipt_mime_type="application/zip",
        user_receipt_message_id=1,
    )

    with pytest.raises(InvalidReceipt):
        PaymentService.validate_receipt(data)


def test_admin_validation_uses_real_telegram_ids(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "123, 456")

    PaymentService.ensure_admin(456)

    with pytest.raises(PermissionError):
        PaymentService.ensure_admin(999)


@pytest.mark.asyncio
async def test_internal_api_key_is_required(monkeypatch):
    key = "a" * 64
    monkeypatch.setattr(settings, "bot_backend_api_key", key)

    await require_internal_api_key(key)

    with pytest.raises(HTTPException) as exc_info:
        await require_internal_api_key("wrong-key")

    assert exc_info.value.status_code == 401


def test_payment_configuration_endpoint(monkeypatch):
    key = "b" * 64
    monkeypatch.setattr(settings, "bot_backend_api_key", key)
    monkeypatch.setattr(settings, "payment_card_number", "0000-0000-0000-0000")
    monkeypatch.setattr(settings, "payment_card_holder", "Test User")
    monkeypatch.setattr(settings, "payment_price_1_month", Decimal("100"))
    monkeypatch.setattr(settings, "payment_price_3_months", Decimal("250"))
    monkeypatch.setattr(settings, "payment_price_6_months", Decimal("450"))
    monkeypatch.setattr(settings, "payment_price_12_months", Decimal("800"))

    client = TestClient(app)
    response = client.get(
        "/payments/configuration",
        headers={"X-Internal-API-Key": key},
    )

    assert response.status_code == 200
    assert [offer["duration_months"] for offer in response.json()["offers"]] == [
        1,
        3,
        6,
        12,
    ]

    unauthorized = client.get(
        "/payments/configuration",
        headers={"X-Internal-API-Key": "wrong"},
    )
    assert unauthorized.status_code == 401
