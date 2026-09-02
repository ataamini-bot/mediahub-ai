import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select


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
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.plan import Plan  # noqa: E402
from app.models.user import User, UserStatus  # noqa: E402
from app.services.payment_offers import get_payment_offer  # noqa: E402
from app.services.plan_management import (  # noqa: E402
    PlanManagementService,
    SystemPlanProtected,
)


def unique_telegram_id() -> int:
    return 8_100_000_000_000 + uuid.uuid4().int % 100_000_000


async def add_superadmin(session, telegram_id: int) -> User:
    user = User(
        telegram_id=telegram_id,
        first_name="Plan owner",
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


@pytest.mark.asyncio
async def test_catalog_migration_keeps_only_free_visible_by_default():
    async with AsyncSessionLocal() as session:
        visible_plans = await PlanManagementService(session).list_plans()

        assert [plan.slug for plan in visible_plans] == ["free"]
        free_plan = visible_plans[0]
        assert free_plan.is_system is True
        assert free_plan.is_active is True
        assert free_plan.duration_days == 0
        assert free_plan.price == Decimal("0")
        assert free_plan.daily_download_limit == 3
        assert free_plan.max_file_size_mb == 300
        assert free_plan.max_quality == 720

        legacy_result = await session.execute(
            select(Plan).where(Plan.slug.in_(("silver", "gold", "premium")))
        )
        legacy_plans = list(legacy_result.scalars())

        assert len(legacy_plans) == 3
        assert all(not plan.is_active for plan in legacy_plans)
        assert all(plan.deleted_at is not None for plan in legacy_plans)

    await engine.dispose()


@pytest.mark.asyncio
async def test_free_plan_limits_are_editable_but_identity_is_protected():
    telegram_id = unique_telegram_id()

    async with AsyncSessionLocal() as session:
        transaction = await session.begin()

        try:
            actor = await add_superadmin(session, telegram_id)
            result = await session.execute(select(Plan).where(Plan.slug == "free"))
            free_plan = result.scalar_one()
            updated = await PlanManagementService(session).update_plan(
                plan_id=free_plan.id,
                actor_user_id=actor.id,
                actor_telegram_id=telegram_id,
                reason="Customize Free limitations",
                daily_download_limit=5,
                daily_limit_supplied=True,
                max_file_size_mb=450,
                max_quality=1080,
                max_concurrent_downloads=2,
                priority_processing=True,
                forced_join_required=False,
            )

            assert updated.daily_download_limit == 5
            assert updated.max_file_size_mb == 450
            assert updated.max_quality == 1080
            assert updated.max_concurrent_downloads == 2
            assert updated.forced_join_required is False

            with pytest.raises(SystemPlanProtected):
                await PlanManagementService(session).update_plan(
                    plan_id=free_plan.id,
                    actor_user_id=actor.id,
                    actor_telegram_id=telegram_id,
                    reason="Unsafe Free identity change",
                    price=Decimal("1000"),
                )
        finally:
            await transaction.rollback()
            await engine.dispose()


@pytest.mark.asyncio
async def test_custom_plan_becomes_dynamic_payment_offer_with_snapshot():
    telegram_id = unique_telegram_id()

    async with AsyncSessionLocal() as session:
        transaction = await session.begin()

        try:
            actor = await add_superadmin(session, telegram_id)
            plan = await PlanManagementService(session).create_plan(
                actor_user_id=actor.id,
                actor_telegram_id=telegram_id,
                reason="Create a custom 45-day plan",
                name=f"Custom {uuid.uuid4().hex[:8]}",
                description="Custom plan integration test",
                duration_days=45,
                price=Decimal("125000"),
                daily_download_limit=None,
                max_file_size_mb=900,
                max_quality=1080,
                max_concurrent_downloads=2,
                priority_processing=True,
                forced_join_required=False,
                sort_order=5,
                is_active=True,
            )
            offer = await get_payment_offer(session, plan.slug)

            assert plan.slug.startswith("plan_")
            assert offer.duration_days == 45
            assert offer.price == Decimal("125000")
            assert offer.daily_download_limit is None
            assert offer.limits_snapshot()["max_file_size_mb"] == 900

            audit_result = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "plan.created",
                    AuditLog.target_id == str(plan.id),
                )
            )
            assert audit_result.scalar_one().details["duration_days"] == 45
        finally:
            await transaction.rollback()
            await engine.dispose()
