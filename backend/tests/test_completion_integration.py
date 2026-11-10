"""PostgreSQL regressions for money, ownership, races, and report boundaries."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, func

from app.db.session import AsyncSessionLocal, engine
from app.models.user import User, UserStatus
from app.models.plan import Plan
from app.models.payment import Payment, PaymentStatus
from app.models.payment_destination import UsdtDestination
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.models.bot_experience import SupportTicket
from app.schemas.payment import PaymentCreate
from app.services.payment import PaymentService, DuplicateTxID
from app.services.bot_experience import BotExperienceService, BotExperienceConflict, BotExperienceNotFound
from app.services.admin_statistics import AdminStatisticsService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def records():
    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        users = [User(telegram_id=8_800_000_000_000 + uuid.uuid4().int % 100_000_000,
            first_name='Phase test', status=UserStatus.ACTIVE, preferred_language='en') for _ in range(2)]
        plans = [Plan(name=f'Phase {suffix} {limit}', name_en=f'Phase {limit}', slug=f'phase_{suffix}_{limit}',
            description='', price=Decimal('10000'), price_usdt=Decimal('2.5'), duration_days=30,
            daily_download_limit=limit, max_file_size_mb=300, max_quality=720,
            max_concurrent_downloads=1, priority_processing=False, forced_join_required=False,
            is_unlimited=False, ai_enabled=False, is_system=False, is_active=True) for limit in (10, 50)]
        destination = UsdtDestination(label='Phase destination', network_name='TRON', network_code='TRC20',
            address='T' + suffix, is_active=True)
        session.add_all(users + plans + [destination]); await session.commit()
        data = SimpleNamespace(users=users, plans=plans, destination=destination)
    try:
        yield data
    finally:
        async with AsyncSessionLocal() as session:
            ids = [u.id for u in users]
            await session.execute(delete(DownloadJob).where(DownloadJob.user_id.in_(ids)))
            await session.execute(delete(Payment).where(Payment.user_id.in_(ids)))
            await session.execute(delete(Subscription).where(Subscription.user_id.in_(ids)))
            await session.execute(delete(SupportTicket).where(SupportTicket.user_id.in_(ids)))
            await session.execute(delete(UsdtDestination).where(UsdtDestination.id == destination.id))
            await session.execute(delete(User).where(User.id.in_(ids)))
            await session.execute(delete(Plan).where(Plan.id.in_([p.id for p in plans])))
            await session.commit()
        await engine.dispose()


async def test_concurrent_usdt_txid_is_accepted_once_and_delivery_failure_keeps_pending(records):
    txid = uuid.uuid4().hex * 2
    async def submit(user):
        async with AsyncSessionLocal() as session:
            try:
                return await PaymentService(session).create_payment(PaymentCreate(
                    telegram_id=user.telegram_id, offer_code=records.plans[0].slug,
                    currency='USDT', usdt_destination_id=records.destination.id, txid=txid))
            except DuplicateTxID:
                return None
    results = await asyncio.wait_for(asyncio.gather(*(submit(u) for u in records.users)), timeout=15)
    accepted = [r for r in results if r]
    assert len(accepted) == 1
    async with AsyncSessionLocal() as session:
        result = await PaymentService(session).mark_delivery_failed(payment_id=accepted[0].payment.id)
        assert result.payment.status == PaymentStatus.PENDING
        assert result.payment.txid == txid
        assert result.payment.receipt_file_id is None


async def test_concurrent_ticket_limit_and_owner_isolation(records):
    user = records.users[0]
    async def create():
        async with AsyncSessionLocal() as session:
            try:
                ticket = await BotExperienceService(session).create_ticket(telegram_id=user.telegram_id,
                    category='payment', body='Please help', telegram_file_id=None, file_type=None)
                await session.commit()
                return ticket.ticket.id
            except BotExperienceConflict:
                await session.rollback()
                return None
    results = await asyncio.wait_for(asyncio.gather(*(create() for _ in range(5))), timeout=15)
    accepted = [id for id in results if id]
    assert len(accepted) == 3
    async with AsyncSessionLocal() as session:
        service = BotExperienceService(session)
        with pytest.raises(BotExperienceNotFound):
            await service.get_ticket(accepted[0], user_telegram_id=records.users[1].telegram_id)
        await service.close_ticket(ticket_id=accepted[0], actor_user_id=records.users[1].id,
            actor_telegram_id=records.users[1].telegram_id)
        await session.commit()
    assert await create()
    async with AsyncSessionLocal() as session:
        with pytest.raises(BotExperienceConflict):
            await BotExperienceService(session).reopen_ticket(ticket_id=accepted[0],
                actor_user_id=records.users[1].id, actor_telegram_id=records.users[1].telegram_id)
        await session.rollback()


async def test_downgrade_waits_and_renewal_preserves_future_purchase(records):
    user = records.users[0]; basic, premium = records.plans
    async with AsyncSessionLocal() as session:
        service = PaymentService(session); service.ensure_admin = AsyncMock()
        async def buy(plan):
            payment = Payment(user_id=user.id, plan_id=plan.id, amount=Decimal('10000'),
                offer_code=plan.slug, duration_days=30, plan_name_snapshot=plan.name,
                plan_limits_snapshot={'daily_download_limit': plan.daily_download_limit},
                status=PaymentStatus.PENDING, payment_method='card',
                receipt_file_id=uuid.uuid4().hex, receipt_file_type='photo', payment_destination_snapshot={})
            session.add(payment); await session.commit()
            return await service.approve(payment_id=payment.id, admin_telegram_id=records.users[1].telegram_id)
        initial = await buy(premium)
        premium_id = initial.subscription.id
        end_before = initial.subscription.expires_at
        queued = await buy(basic)
        assert queued.payment.subscription_change_type == 'downgrade'
        assert queued.subscription.status == SubscriptionStatus.SCHEDULED
        assert queued.subscription.started_at == end_before
        queued_id = queued.subscription.id
        renewal = await buy(premium)
        assert renewal.subscription.id == premium_id
        assert renewal.subscription.daily_download_limit == 100
        await session.refresh(queued.subscription)
        assert queued.subscription.id == queued_id
        assert queued.subscription.started_at == end_before + timedelta(days=30)
        assert queued.subscription.expires_at == end_before + timedelta(days=60)
        # A scheduled purchase becomes effective by its dates without a worker race.
        renewal.subscription.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        queued.subscription.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        await session.commit()
        details = await service.get_subscription_details(telegram_id=user.telegram_id)
        assert details['plan_slug'] == basic.slug


async def test_purchase_without_current_entitlement_appends_after_future_schedule(records):
    user = records.users[0]
    basic, premium = records.plans
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        expired = Subscription(
            user_id=user.id, plan_id=premium.id, status=SubscriptionStatus.ACTIVE,
            started_at=now - timedelta(days=30), expires_at=now - timedelta(hours=1),
            daily_download_limit=premium.daily_download_limit, auto_renew=False,
        )
        future = Subscription(
            user_id=user.id, plan_id=premium.id, status=SubscriptionStatus.SCHEDULED,
            started_at=now + timedelta(days=30), expires_at=now + timedelta(days=60),
            daily_download_limit=premium.daily_download_limit, auto_renew=False,
        )
        payment = Payment(
            user_id=user.id, plan_id=basic.id, amount=Decimal('10000'),
            offer_code=basic.slug, duration_days=30, plan_name_snapshot=basic.name,
            plan_limits_snapshot={'daily_download_limit': basic.daily_download_limit},
            status=PaymentStatus.PENDING, payment_method='card',
            receipt_file_id=uuid.uuid4().hex, receipt_file_type='photo',
            payment_destination_snapshot={},
        )
        session.add_all([expired, future, payment])
        await session.commit()
        service = PaymentService(session)
        service.ensure_admin = AsyncMock()
        result = await service.approve(payment_id=payment.id, admin_telegram_id=records.users[1].telegram_id)
        assert result.subscription.status == SubscriptionStatus.SCHEDULED
        assert result.subscription.started_at == future.expires_at
        assert result.payment.subscription_change_type == 'scheduled'


async def test_download_site_pages_and_periods_count_completed_files(records):
    now = datetime(2027, 3, 31, 12, tzinfo=timezone.utc)
    async with AsyncSessionLocal() as session:
        jobs = [DownloadJob(user_id=records.users[0].id, source_url=f'https://site{i}.example/video',
            status=DownloadJobStatus.COMPLETED, completed_at=now-timedelta(hours=1), file_size=10) for i in range(10)]
        jobs += [DownloadJob(user_id=records.users[0].id, source_url='https://m.youtube.com/watch?v=a', status=DownloadJobStatus.COMPLETED,
            completed_at=now-timedelta(days=20), file_size=50),
            DownloadJob(user_id=records.users[0].id, source_url='https://site0.example/youtube.com', status=DownloadJobStatus.FAILED,
            created_at=now-timedelta(hours=2))]
        session.add_all(jobs); await session.commit()
        service = AdminStatisticsService(session); service.now = now
        week = await service.downloads('7d', 1)
        week2 = await service.downloads('7d', 2)
        assert week['successful'] == 10 and week['failed'] == 1
        assert week['site_total'] == 10
        assert sum(row['count'] for row in week['by_site'] + week2['by_site']) == 10
        assert all(row['site'].startswith('site') for row in week['by_site'] + week2['by_site'])
        month = await service.downloads('1mo')
        assert month['successful'] == 11 and month['total_file_size'] == 150
        assert month['site_total'] == 11


async def test_financial_average_and_renewal_keep_currencies_separate(records):
    now = datetime(2027, 3, 31, 12, tzinfo=timezone.utc)
    async with AsyncSessionLocal() as session:
        for method, amount, change in [('card', '10000', 'new'), ('usdt', '2.5', 'new'), ('usdt', '1.5', 'renewal')]:
            session.add(Payment(user_id=records.users[0].id, plan_id=records.plans[0].id,
                amount=Decimal(amount), offer_code=records.plans[0].slug, duration_days=30,
                plan_name_snapshot='Phase', plan_limits_snapshot={}, payment_method=method,
                payment_destination_snapshot={}, status=PaymentStatus.APPROVED,
                subscription_change_type=change, reviewed_at=now-timedelta(hours=1),
                receipt_file_id='phase', receipt_file_type='photo'))
        await session.commit()
        service = AdminStatisticsService(session); service.now = now
        result = await service._currency_finance(now-timedelta(days=7))
        assert Decimal(result['IRT']['average']) == 10000
        assert Decimal(result['USDT']['total']) == 4
        assert Decimal(result['USDT']['average']) == 2
        assert Decimal(result['USDT']['renewal']) == Decimal('1.5')
        assert result['USDT']['successful'] == 2
