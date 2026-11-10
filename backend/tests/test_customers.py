import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.api.customers import CustomerChange
from app.db.session import AsyncSessionLocal, engine
from app.models.admin import AdminAccount
from app.models.customer_action import CustomerAction
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.services.admin_access import AdminAccessDenied
from app.services.customers import CustomerConflict, CustomerService
from app.services.download_access import DownloadAccessService, DownloadUserBlocked, WeeklyDownloadLimitReached, WeeklyConversionLimitReached

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def records():
    async with AsyncSessionLocal() as db:
        users = [User(telegram_id=8_600_000_000_000 + uuid.uuid4().int % 100_000_000,
            first_name="Customer regression", status=UserStatus.ACTIVE) for _ in range(3)]
        plan = Plan(name="Support plan", name_en="Support plan", slug="support_" + uuid.uuid4().hex,
            description="", price=Decimal(100), duration_days=30, daily_download_limit=3,
            download_limit_period="daily", max_file_size_mb=300, max_quality=720,
            max_concurrent_downloads=1, priority_processing=False, forced_join_required=False,
            is_unlimited=False, ai_enabled=False, is_system=False, is_active=True)
        db.add_all(users + [plan])
        await db.flush()
        db.add(AdminAccount(user_id=users[0].id, is_superadmin=True, is_active=True, created_by_user_id=users[0].id))
        await db.commit()
    try:
        yield SimpleNamespace(admin=users[0], user=users[1], outsider=users[2], plan=plan)
    finally:
        async with AsyncSessionLocal() as db:
            ids = [u.id for u in users]
            await db.execute(delete(DownloadJob).where(DownloadJob.user_id.in_(ids)))
            await db.execute(delete(Subscription).where(Subscription.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(delete(Plan).where(Plan.id == plan.id))
            await db.commit()
        await engine.dispose()


async def change_for(records, action, **kwargs):
    async with AsyncSessionLocal() as db:
        revision = (await CustomerService(db).profile(records.user.telegram_id))["revision"]
    return CustomerChange(actor_telegram_id=records.admin.telegram_id, request_id=uuid.uuid4(),
        expected_revision=revision, action=action, reason="Customer support regression", **kwargs)


async def test_unauthorized_actor_and_stale_forms_cannot_modify_customer(records):
    data = await change_for(records, "block")
    async with AsyncSessionLocal() as db:
        with pytest.raises(AdminAccessDenied):
            await CustomerService(db).change(records.user.telegram_id,
                data.model_copy(update={"actor_telegram_id": records.outsider.telegram_id}))
    async with AsyncSessionLocal() as db:
        await CustomerService(db).change(records.user.telegram_id, data)
    async with AsyncSessionLocal() as db:
        with pytest.raises(CustomerConflict):
            await CustomerService(db).change(records.user.telegram_id,
                data.model_copy(update={"request_id": uuid.uuid4(), "action": "unblock"}))
        with pytest.raises(DownloadUserBlocked):
            await DownloadAccessService(db).authorize_job(telegram_id=records.user.telegram_id, quality="360p", estimated_size_bytes=1)


async def test_concurrent_duplicate_grant_is_applied_once_and_old_purchase_preserved(records):
    data = await change_for(records, "grant", plan_id=records.plan.id, days=30)
    async def apply():
        async with AsyncSessionLocal() as db:
            return await CustomerService(db).change(records.user.telegram_id, data)
    results = await asyncio.wait_for(asyncio.gather(apply(), apply()), timeout=15)
    assert sorted(r["replayed"] for r in results) == [False, True]
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id == records.user.id)) == 1
    scheduled = await change_for(records, "grant", plan_id=records.plan.id, days=10)
    async with AsyncSessionLocal() as db:
        await CustomerService(db).change(records.user.telegram_id, scheduled)
        rows = (await db.scalars(select(Subscription).where(Subscription.user_id == records.user.id).order_by(Subscription.started_at))).all()
        assert len(rows) == 2
        assert rows[1].started_at == rows[0].expires_at
        old_end = rows[1].expires_at
        first_id, second_id = rows[0].id, rows[1].id
    extension = await change_for(records, "extend", subscription_id=first_id, days=7)
    async with AsyncSessionLocal() as db:
        await CustomerService(db).change(records.user.telegram_id, extension)
        first = await db.get(Subscription, first_id)
        second = await db.get(Subscription, second_id)
        assert second.started_at == first.expires_at
        assert second.expires_at == old_end + timedelta(days=7)
    cancel = await change_for(records, "cancel", subscription_id=first_id)
    async with AsyncSessionLocal() as db:
        await CustomerService(db).change(records.user.telegram_id, cancel)
        assert (await db.get(Subscription, first_id)).status == SubscriptionStatus.CANCELLED
        assert (await db.get(Subscription, second_id)).status == SubscriptionStatus.SCHEDULED


async def test_reset_restores_free_allowances_without_erasing_deliveries(records):
    async with AsyncSessionLocal() as db:
        free = await db.scalar(select(Plan).where(Plan.slug == "free"))
        now = datetime.now(timezone.utc)
        for index in range(free.daily_download_limit + 1):
            db.add(DownloadJob(user_id=records.user.id, source_url="https://example.test/video",
                media_type="convert" if index == free.daily_download_limit else "video",
                quality="360p", status=DownloadJobStatus.COMPLETED, delivered_at=now))
        await db.commit()
        with pytest.raises(WeeklyDownloadLimitReached):
            await DownloadAccessService(db).authorize_job(telegram_id=records.user.telegram_id, quality="360p", estimated_size_bytes=1)
        with pytest.raises(WeeklyConversionLimitReached):
            await DownloadAccessService(db).authorize_job(telegram_id=records.user.telegram_id, quality=None, estimated_size_bytes=1, media_type="convert")
    reset = await change_for(records, "reset_quota")
    async with AsyncSessionLocal() as db:
        await CustomerService(db).change(records.user.telegram_id, reset)
        service = DownloadAccessService(db)
        await service.authorize_job(telegram_id=records.user.telegram_id, quality="360p", estimated_size_bytes=1)
        await service.authorize_job(telegram_id=records.user.telegram_id, quality=None, estimated_size_bytes=1, media_type="convert")
        assert await db.scalar(select(func.count()).select_from(DownloadJob).where(DownloadJob.user_id == records.user.id)) == free.daily_download_limit + 1


async def test_actions_cannot_change_an_administrator(records):
    async with AsyncSessionLocal() as db:
        revision = await CustomerService(db).revision(await db.get(User, records.admin.id))
        change = CustomerChange(actor_telegram_id=records.admin.telegram_id, request_id=uuid.uuid4(),
            expected_revision=revision, action="block", reason="Forbidden admin block")
        with pytest.raises(CustomerConflict):
            await CustomerService(db).change(records.admin.telegram_id, change)
