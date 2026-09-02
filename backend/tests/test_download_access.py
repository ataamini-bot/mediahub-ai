import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from unittest.mock import AsyncMock, Mock

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
from app.models.download_job import (  # noqa: E402
    DownloadJob,
    DownloadJobStatus,
)
from app.models.plan import Plan  # noqa: E402
from app.models.subscription import (  # noqa: E402
    Subscription,
    SubscriptionStatus,
)
from app.models.user import User, UserStatus  # noqa: E402
from app.services.download_access import (  # noqa: E402
    ConcurrentDownloadLimitReached,
    DailyDownloadLimitReached,
    DownloadAccessService,
    DownloadFileSizeLimitExceeded,
    DownloadQualityLimitExceeded,
    quota_day_start_utc,
)
from app.workers.tasks.download import (  # noqa: E402
    MAX_DOWNLOAD_BYTES,
    _resolve_max_download_bytes,
)


def unique_telegram_id() -> int:
    return 8_200_000_000_000 + uuid.uuid4().int % 100_000_000


async def add_user(session, *, admin: bool = False) -> User:
    user = User(
        telegram_id=unique_telegram_id(),
        first_name="Download limits test",
        status=UserStatus.ACTIVE,
        is_admin=admin,
    )
    session.add(user)
    await session.flush()

    if admin:
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


async def add_custom_subscription(
    session,
    user: User,
    *,
    daily_limit: int | None = 2,
    concurrency: int = 1,
) -> Plan:
    now = datetime.now(timezone.utc)
    plan = Plan(
        name=f"Test plan {uuid.uuid4().hex[:8]}",
        slug=f"plan_test_{uuid.uuid4().hex[:16]}",
        description="Download entitlement integration test",
        price=Decimal("1000"),
        duration_days=10,
        daily_download_limit=daily_limit,
        max_file_size_mb=500,
        max_quality=1080,
        max_concurrent_downloads=concurrency,
        priority_processing=True,
        forced_join_required=False,
        is_unlimited=daily_limit is None,
        ai_enabled=False,
        sort_order=0,
        is_system=False,
        is_active=True,
    )
    session.add(plan)
    await session.flush()
    session.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            started_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=10),
            auto_renew=False,
        )
    )
    await session.flush()
    return plan


def make_job(
    user: User,
    *,
    status: DownloadJobStatus,
    delivered_at: datetime | None = None,
) -> DownloadJob:
    return DownloadJob(
        user_id=user.id,
        source_url="https://example.com/video",
        quality="720p",
        media_type="video",
        status=status,
        progress=100 if status == DownloadJobStatus.COMPLETED else 0,
        delivered_at=delivered_at,
    )


@pytest.mark.asyncio
async def test_free_plan_rejects_quality_and_estimated_size_above_limits():
    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            user = await add_user(session)
            service = DownloadAccessService(session)

            with pytest.raises(DownloadQualityLimitExceeded):
                await service.authorize_job(
                    telegram_id=user.telegram_id,
                    quality="1080p",
                    estimated_size_bytes=None,
                )

            with pytest.raises(DownloadFileSizeLimitExceeded):
                await service.authorize_job(
                    telegram_id=user.telegram_id,
                    quality="720p",
                    estimated_size_bytes=301 * 1024 * 1024,
                )
        finally:
            await transaction.rollback()
            await engine.dispose()


@pytest.mark.asyncio
async def test_custom_plan_enforces_concurrency_and_daily_delivery_quota():
    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            user = await add_user(session)
            await add_custom_subscription(
                session,
                user,
                daily_limit=2,
                concurrency=1,
            )
            service = DownloadAccessService(session)

            active_job = make_job(user, status=DownloadJobStatus.PENDING)
            session.add(active_job)
            await session.flush()

            with pytest.raises(ConcurrentDownloadLimitReached):
                await service.authorize_job(
                    telegram_id=user.telegram_id,
                    quality="1080p",
                    estimated_size_bytes=400 * 1024 * 1024,
                )

            active_job.status = DownloadJobStatus.FAILED
            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    make_job(
                        user,
                        status=DownloadJobStatus.COMPLETED,
                        delivered_at=now,
                    ),
                    make_job(
                        user,
                        status=DownloadJobStatus.COMPLETED,
                        delivered_at=now,
                    ),
                ]
            )
            await session.flush()

            with pytest.raises(DailyDownloadLimitReached):
                await service.authorize_job(
                    telegram_id=user.telegram_id,
                    quality="1080p",
                    estimated_size_bytes=400 * 1024 * 1024,
                )
        finally:
            await transaction.rollback()
            await engine.dispose()


@pytest.mark.asyncio
async def test_active_admin_bypasses_commercial_plan_limits():
    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            user = await add_user(session, admin=True)
            session.add_all(
                [
                    make_job(user, status=DownloadJobStatus.PENDING),
                    make_job(user, status=DownloadJobStatus.PROCESSING),
                    make_job(
                        user,
                        status=DownloadJobStatus.COMPLETED,
                        delivered_at=datetime.now(timezone.utc),
                    ),
                ]
            )
            await session.flush()

            entitlement = await DownloadAccessService(session).authorize_job(
                telegram_id=user.telegram_id,
                quality="4K",
                estimated_size_bytes=1800 * 1024 * 1024,
            )

            assert entitlement.is_admin_bypass is True
            assert entitlement.daily_download_limit is None
            assert entitlement.max_quality is None
            assert entitlement.priority_processing is True
        finally:
            await transaction.rollback()
            await engine.dispose()


def test_quality_parser_supports_bot_labels():
    assert DownloadAccessService.parse_quality_height("720p") == 720
    assert DownloadAccessService.parse_quality_height("2K") == 1440
    assert DownloadAccessService.parse_quality_height("4K") == 2160
    assert DownloadAccessService.parse_quality_height(None) is None


def test_daily_quota_uses_tehran_calendar_day():
    now = datetime(2026, 9, 2, 0, 15, tzinfo=timezone.utc)

    assert quota_day_start_utc(now, "Asia/Tehran") == datetime(
        2026,
        9,
        1,
        20,
        30,
        tzinfo=timezone.utc,
    )


def test_worker_uses_snapshot_file_size_with_technical_cap():
    assert _resolve_max_download_bytes({"max_file_size_mb": 300}) == (
        300 * 1024 * 1024
    )
    assert _resolve_max_download_bytes({"max_file_size_mb": 5000}) == (
        MAX_DOWNLOAD_BYTES
    )
    assert _resolve_max_download_bytes(None) == MAX_DOWNLOAD_BYTES


@pytest.mark.asyncio
async def test_delivery_confirmation_is_idempotent():
    job = DownloadJob(
        id=991,
        source_url="https://example.com/video",
        status=DownloadJobStatus.COMPLETED,
        progress=100,
    )
    result = Mock()
    result.scalar_one_or_none.return_value = job
    session = AsyncMock()
    session.execute.return_value = result

    service = DownloadAccessService(session)
    delivered = await service.mark_delivered(job.id)

    assert delivered.delivered_at is not None
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(job)

    session.commit.reset_mock()
    session.refresh.reset_mock()
    await service.mark_delivered(job.id)

    session.commit.assert_not_awaited()
    session.refresh.assert_not_awaited()
