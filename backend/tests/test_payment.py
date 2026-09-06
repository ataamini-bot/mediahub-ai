import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete


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
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.core.internal_auth import require_internal_api_key  # noqa: E402
from app.main import app  # noqa: E402
from app.models.payment import Payment, PaymentStatus  # noqa: E402
from app.schemas.payment import PaymentCreate  # noqa: E402
from app.schemas.payment import PaymentUserResponse  # noqa: E402
from app.services.payment import (  # noqa: E402
    InvalidReceipt,
    PaymentService,
    add_duration_days,
    combine_daily_download_limits,
)
from app.models.plan import Plan  # noqa: E402
from app.models.subscription import Subscription  # noqa: E402
from app.models.user import User, UserStatus  # noqa: E402
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


def test_renewal_stacks_finite_daily_download_quota():
    assert combine_daily_download_limits(50, 50) == 100
    assert combine_daily_download_limits(100, 50) == 150


def test_renewal_keeps_unlimited_daily_download_quota():
    assert combine_daily_download_limits(None, 50) is None
    assert combine_daily_download_limits(50, None) is None


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


def test_payment_offer_uses_usdt_price_for_english_catalog():
    plan = Plan(
        id=92,
        name="International",
        slug="plan_international",
        price=Decimal("125000"),
        price_usdt=Decimal("2.7500"),
        duration_days=30,
        daily_download_limit=50,
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

    offer = PaymentOffer.from_plan(plan, currency="USDT")

    assert offer.price == Decimal("2.7500")


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


def test_payment_user_response_includes_home_menu_context():
    response = PaymentUserResponse.model_validate(
        SimpleNamespace(
            telegram_id=123456789,
            username="sample",
            first_name="Sample",
            last_name="User",
            language_code="en-US",
            preferred_language="fa",
            is_admin=True,
        )
    )

    assert response.effective_language == "fa"
    assert response.is_admin is True
    assert response.model_dump()["effective_language"] == "fa"


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


def test_usdt_payment_requires_selected_destination():
    with pytest.raises(ValueError):
        PaymentCreate(
            telegram_id=123,
            offer_code="plan_test",
            currency="USDT",
            receipt_file_id="file-id",
            receipt_file_type="photo",
        )


def test_usdt_payment_accepts_selected_destination():
    data = PaymentCreate(
        telegram_id=123,
        offer_code="plan_test",
        currency="USDT",
        usdt_destination_id=7,
        receipt_file_id="file-id",
        receipt_file_type="photo",
    )

    assert data.usdt_destination_id == 7


def test_usdt_payment_rejects_mixed_card_destination():
    with pytest.raises(ValueError):
        PaymentCreate(
            telegram_id=123,
            offer_code="plan_test",
            currency="USDT",
            payment_card_id=3,
            usdt_destination_id=7,
            receipt_file_id="file-id",
            receipt_file_type="photo",
        )


@pytest.mark.asyncio
async def test_approving_second_payment_stacks_duration_and_daily_quota():
    suffix = uuid.uuid4().hex
    user_id: int | None = None
    plan_id: int | None = None

    async with AsyncSessionLocal() as session:
        try:
            user = User(
                telegram_id=8_500_000_000_000 + uuid.uuid4().int % 100_000_000,
                first_name="Renewal test",
                status=UserStatus.ACTIVE,
                is_admin=False,
            )
            plan = Plan(
                name=f"Renewal plan {suffix[:8]}",
                slug=f"plan_renewal_{suffix[:16]}",
                price=Decimal("79000"),
                duration_days=30,
                daily_download_limit=50,
                max_file_size_mb=900,
                max_quality=1080,
                max_concurrent_downloads=1,
                priority_processing=False,
                forced_join_required=False,
                is_unlimited=False,
                ai_enabled=False,
                sort_order=0,
                is_system=False,
                is_active=True,
            )
            session.add_all([user, plan])
            await session.flush()
            user_id = user.id
            plan_id = plan.id

            def payment(number: int) -> Payment:
                return Payment(
                    user_id=user.id,
                    plan_id=plan.id,
                    amount=Decimal("79000"),
                    offer_code=plan.slug,
                    duration_months=None,
                    duration_days=30,
                    plan_name_snapshot=plan.name,
                    plan_limits_snapshot={
                        "daily_download_limit": 50,
                        "max_file_size_mb": 900,
                        "max_quality": 1080,
                        "max_concurrent_downloads": 1,
                        "priority_processing": False,
                        "forced_join_required": False,
                    },
                    status=PaymentStatus.PENDING,
                    receipt_file_id=f"receipt-{suffix}-{number}",
                    receipt_file_unique_id=f"unique-{suffix}-{number}",
                    receipt_file_type="photo",
                    payment_method="card",
                    payment_destination_snapshot={},
                )

            first_payment = payment(1)
            session.add(first_payment)
            await session.commit()

            service = PaymentService(session)
            service.ensure_admin = AsyncMock()
            first_result = await service.approve(
                payment_id=first_payment.id,
                admin_telegram_id=123456789,
            )
            assert first_result.subscription is not None
            assert first_result.subscription.daily_download_limit == 50

            second_payment = payment(2)
            session.add(second_payment)
            await session.commit()
            second_result = await service.approve(
                payment_id=second_payment.id,
                admin_telegram_id=123456789,
            )

            assert second_result.subscription is not None
            assert second_result.subscription.id == first_result.subscription.id
            assert second_result.subscription.daily_download_limit == 100
            assert (
                second_result.subscription.expires_at
                - second_result.subscription.started_at
            ).days == 60
        finally:
            await session.rollback()
            if user_id is not None:
                await session.execute(
                    delete(Payment).where(Payment.user_id == user_id)
                )
                await session.execute(
                    delete(Subscription).where(Subscription.user_id == user_id)
                )
            if plan_id is not None:
                await session.execute(delete(Plan).where(Plan.id == plan_id))
            if user_id is not None:
                await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


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

    async def fake_configuration(
        _db,
        *,
        select_destination=True,
        language="fa",
    ):
        assert select_destination is True
        assert language == "fa"
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
