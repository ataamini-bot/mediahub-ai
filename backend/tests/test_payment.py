import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

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
    add_duration_days,
)
from app.models.plan import Plan  # noqa: E402
from app.services.payment_offers import PaymentOffer  # noqa: E402


def test_add_duration_days_uses_exact_custom_duration():
    start = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)

    assert add_duration_days(start, 45) == datetime(
        2026,
        3,
        17,
        12,
        tzinfo=timezone.utc,
    )


def test_add_duration_days_rejects_nonpositive_duration():
    with pytest.raises(ValueError):
        add_duration_days(datetime.now(timezone.utc), 0)


def test_payment_offer_snapshots_custom_plan_limits():
    plan = Plan(
        id=91,
        name="پلن ویژه ۴۵ روزه",
        slug="plan_test",
        price=Decimal("125000"),
        duration_days=45,
        daily_download_limit=75,
        max_file_size_mb=900,
        max_quality=1080,
        max_concurrent_downloads=2,
        priority_processing=True,
        forced_join_required=False,
        is_unlimited=False,
        ai_enabled=False,
        sort_order=0,
        is_system=False,
        is_active=True,
    )

    offer = PaymentOffer.from_plan(plan)

    assert offer.duration_days == 45
    assert offer.price == Decimal("125000")
    assert offer.limits_snapshot() == {
        "daily_download_limit": 75,
        "max_file_size_mb": 900,
        "max_quality": 1080,
        "max_concurrent_downloads": 2,
        "priority_processing": True,
        "forced_join_required": False,
    }


def test_receipt_validation_rejects_large_file(monkeypatch):
    monkeypatch.setattr(settings, "payment_receipt_max_size_mb", 1)
    data = PaymentCreate(
        telegram_id=123,
        offer_code="plan_test",
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
        offer_code="plan_test",
        receipt_file_id="file-id",
        receipt_file_type="document",
        receipt_file_size=100,
        receipt_mime_type="application/zip",
        user_receipt_message_id=1,
    )

    with pytest.raises(InvalidReceipt):
        PaymentService.validate_receipt(data)


@pytest.mark.asyncio
async def test_admin_validation_uses_database_rbac(monkeypatch):
    access_service = AsyncMock()

    def access_factory(session):
        return access_service

    monkeypatch.setattr(
        "app.services.payment.AdminAccessService",
        access_factory,
    )
    service = PaymentService(session=AsyncMock())

    await service.ensure_admin(456)

    access_service.require_permission.assert_awaited_once_with(
        456,
        "payments.review",
    )


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

    async def fake_configuration(_db, *, select_destination=True):
        assert select_destination is True
        return {
            "offers": [
                {
                    "code": "plan_test",
                    "label": "پلن تست",
                    "duration_days": 45,
                    "price": Decimal("125000"),
                    "currency": "IRT",
                    "daily_download_limit": 75,
                    "max_file_size_mb": 900,
                    "max_quality": 1080,
                    "max_concurrent_downloads": 2,
                    "priority_processing": True,
                    "forced_join_required": False,
                }
            ],
            "destination": {
                "card_number": "0000000000000000",
                "card_holder": "Test User",
                "bank_name": None,
            },
            "receipt": {
                "max_size_mb": 10,
                "allowed_types": ["photo", "application/pdf"],
            },
        }

    monkeypatch.setattr(
        "app.api.payments.get_payment_configuration",
        fake_configuration,
    )

    client = TestClient(app)
    response = client.get(
        "/payments/configuration",
        headers={"X-Internal-API-Key": key},
    )

    assert response.status_code == 200
    assert response.json()["offers"][0]["duration_days"] == 45

    unauthorized = client.get(
        "/payments/configuration",
        headers={"X-Internal-API-Key": "wrong"},
    )
    assert unauthorized.status_code == 401
