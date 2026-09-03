import os
import uuid

import pytest


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


from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.models.admin import AdminAccount  # noqa: E402
from app.models.user import User, UserStatus  # noqa: E402
from app.services.application_settings import (  # noqa: E402
    ApplicationSettingsService,
    SettingValidationError,
)
from app.services.managed_settings import get_managed_setting  # noqa: E402
from app.services.payment_management import (  # noqa: E402
    PaymentManagementService,
    payment_card_snapshot,
)


def unique_telegram_id() -> int:
    return 8_300_000_000_000 + uuid.uuid4().int % 100_000_000


async def add_superadmin(session) -> User:
    user = User(
        telegram_id=unique_telegram_id(),
        first_name="Finance owner",
        status=UserStatus.ACTIVE,
        is_admin=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        AdminAccount(
            user_id=user.id,
            is_superadmin=True,
            is_active=True,
            created_by_user_id=user.id,
        )
    )
    await session.flush()
    return user


def unique_card_number(prefix: str) -> str:
    suffix = str(uuid.uuid4().int % 10_000_000_000).zfill(10)
    return f"{prefix}{suffix}"


@pytest.mark.asyncio
async def test_database_cards_rotate_and_snapshot_independently():
    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            actor = await add_superadmin(session)
            service = PaymentManagementService(session)
            first = await service.create_card(
                actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id,
                label="Primary",
                card_number=unique_card_number("990001"),
                card_holder="First Holder",
                bank_name="Bank A",
                sort_order=0,
                is_active=True,
            )
            second = await service.create_card(
                actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id,
                label="Secondary",
                card_number=unique_card_number("990002"),
                card_holder="Second Holder",
                bank_name="Bank B",
                sort_order=1,
                is_active=True,
            )

            selected = [await service.select_card() for _ in range(4)]

            assert [card.id for card in selected] == [
                first.id,
                second.id,
                first.id,
                second.id,
            ]
            snapshot = payment_card_snapshot(first)
            await service.update_card(
                card_id=first.id,
                actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id,
                changes={"card_holder": "Changed Holder"},
            )
            assert snapshot["card_holder"] == "First Holder"
            assert first.card_holder == "Changed Holder"
        finally:
            await transaction.rollback()
            await engine.dispose()


@pytest.mark.asyncio
async def test_usdt_destination_crud_stores_public_operational_data():
    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            actor = await add_superadmin(session)
            service = PaymentManagementService(session)
            address = f"T{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"
            destination = await service.create_usdt_destination(
                actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id,
                data={
                    "label": "TRC20 main",
                    "network_name": "TRON",
                    "network_code": "TRC20",
                    "address": address,
                    "asset_symbol": "USDT",
                    "contract_address": None,
                    "explorer_url": "https://tronscan.org",
                    "confirmations_required": 20,
                    "sort_order": 0,
                    "is_active": True,
                },
            )
            await service.update_usdt_destination(
                destination_id=destination.id,
                actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id,
                changes={"confirmations_required": 25},
            )
            assert destination.confirmations_required == 25
            await service.delete_usdt_destination(
                destination_id=destination.id,
                actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id,
            )
            await session.flush()
            assert await service.list_usdt_destinations() == []
        finally:
            await transaction.rollback()
            await engine.dispose()


@pytest.mark.asyncio
async def test_managed_runtime_settings_are_typed_and_versioned():
    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            actor = await add_superadmin(session)
            service = ApplicationSettingsService(session)
            setting = await service.get_setting("payments.receipt_max_size_mb")
            original_version = setting.version
            updated = await service.set_value(
                key=setting.key,
                category=setting.category,
                value=17,
                is_sensitive=False,
                actor_user_id=actor.id,
                actor_telegram_id=actor.telegram_id,
                description=setting.description,
                expected_version=original_version,
            )
            assert updated.version == original_version + 1
            assert await get_managed_setting(
                session,
                "payments.receipt_max_size_mb",
            ) == 17

            with pytest.raises(SettingValidationError):
                await service.set_value(
                    key=setting.key,
                    category=setting.category,
                    value=51,
                    is_sensitive=False,
                    actor_user_id=actor.id,
                    actor_telegram_id=actor.telegram_id,
                    expected_version=updated.version,
                )
        finally:
            await transaction.rollback()
            await engine.dispose()
