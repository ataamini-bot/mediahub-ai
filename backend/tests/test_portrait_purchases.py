"""Real PostgreSQL product, payment and allowance boundaries; no paid API calls."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from app.api.media_tools import ToolSubmit
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.admin import AdminAccount
from app.models.app_setting import ApplicationSetting
from app.models.credit import CreditEntry
from app.models.media_tool import MediaToolRequest
from app.models.payment import Payment, PaymentStatus
from app.models.payment_order import PaymentOrder
from app.models.payment_destination import PaymentCard, UsdtDestination
from app.models.plan import Plan
from app.models.portrait_credit import PortraitCredit
from app.models.subscription import Subscription
from app.models.user import User
from app.schemas.credit import CreditChange
from app.schemas.payment import PaymentCreate, PaymentOrderCreate, PaymentResponse
from app.services.application_settings import ApplicationSettingsService
from app.services.credit import CreditService
from app.services.download_access import quota_window_start_utc
from app.services.media_tool_config import configure_tool
from app.services.media_tool_files import folder
from app.services.media_tools import MediaToolError, submit
from app.services.payment import PaymentService, PaymentConflict
from app.services.payment_orders import PaymentOrderService, PaymentOrderError
from app.services.portrait_purchases import balance, refund_failed_credit


@pytest_asyncio.fixture
async def records(tmp_path, monkeypatch):
    assert settings.app_env == 'test', 'Use a disposable database'
    monkeypatch.setenv('DOWNLOAD_DIR',str(tmp_path))
    async with AsyncSessionLocal() as db:
        users=[User(telegram_id=7_600_000_000_000+uuid4().int%100_000_000,first_name='Portrait test',preferred_language='en' if i==2 else 'fa') for i in range(3)]
        card=PaymentCard(label='Portrait card',card_number=str(10**15+uuid4().int%(9*10**15)),card_holder='Test',is_active=True)
        wallet=UsdtDestination(label='Portrait wallet',network_name='TRON',network_code='TRC20',address='T'+uuid4().hex,is_active=True)
        plan=Plan(name='Tools plan',name_en='Tools plan',slug='tools_'+uuid4().hex,price=100, duration_days=30,
                  daily_download_limit=3, max_concurrent_downloads=1, is_active=True, is_system=False,
                  tool_limits={'portrait':{'limit':0,'period':'daily'},'music':{'limit':1,'period':'weekly'}})
        db.add_all([*users,card,wallet,plan]);await db.flush()
        db.add(AdminAccount(user_id=users[0].id,is_active=True,is_superadmin=True,created_by_user_id=users[0].id))
        # User 1 has a paid plan which forbids portraits; user 2 has no subscription.
        db.add(Subscription(user_id=users[1].id,plan_id=plan.id,started_at=datetime.now(timezone.utc)-timedelta(days=1),
                            expires_at=datetime.now(timezone.utc)+timedelta(days=29),daily_download_limit=3))
        service=ApplicationSettingsService(db)
        for key,value in [('portrait.price_irt','25000'),('portrait.price_usdt','1.2500')]:
            await service.set_value(key=key,category='payments',value=value,is_sensitive=False,actor_user_id=users[0].id,actor_telegram_id=users[0].telegram_id)
        for kind in ('portrait','music'):
            await configure_tool(db,{'kind':kind,'enabled':True,'token':'test-key','global_daily':100},users[0])
        await db.commit()
    try:
        yield SimpleNamespace(users=users,card=card,wallet=wallet,plan=plan)
    finally:
        async with AsyncSessionLocal() as db:
            ids=[u.id for u in users]
            await db.execute(delete(PortraitCredit).where(PortraitCredit.user_id.in_(ids)))
            await db.execute(delete(MediaToolRequest).where(MediaToolRequest.user_id.in_(ids)))
            await db.execute(text('TRUNCATE credit_notices, credit_entries, credit_accounts'))
            await db.execute(delete(Payment).where(Payment.user_id.in_(ids)))
            await db.execute(delete(PaymentOrder).where(PaymentOrder.user_id.in_(ids)))
            await db.execute(delete(Subscription).where(Subscription.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(delete(Plan).where(Plan.id==plan.id))
            await db.execute(delete(PaymentCard).where(PaymentCard.id==card.id))
            await db.execute(delete(UsdtDestination).where(UsdtDestination.id==wallet.id))
            await db.execute(delete(ApplicationSetting).where(ApplicationSetting.key.like('tools.%')))
            await db.execute(update(ApplicationSetting).where(ApplicationSetting.key.like('portrait.price_%')).values(value_json='0'))
            await db.commit()
        await engine.dispose()


async def order(r, user=1, method=None):
    kwargs={'telegram_id':r.users[user].telegram_id,'offer_code':'portrait-once','currency':'USDT' if user==2 else 'IRT'}
    if method:kwargs['method']=method
    if user==2 and method!='credit':kwargs['usdt_destination_id']=r.wallet.id
    async with AsyncSessionLocal() as db:
        return await PaymentOrderService(db).start(PaymentOrderCreate(**kwargs))


async def receipt(r, checkout, user=1):
    kwargs={'telegram_id':r.users[user].telegram_id,'offer_code':'portrait-once','order_id':checkout.id,'currency':checkout.currency}
    if user==2:kwargs.update(usdt_destination_id=r.wallet.id,txid=uuid4().hex*2)
    else:kwargs.update(receipt_file_id=uuid4().hex,receipt_file_unique_id=uuid4().hex,receipt_file_type='photo')
    async with AsyncSessionLocal() as db:
        return (await PaymentService(db).create_payment(PaymentCreate(**kwargs))).payment


async def approve(r, payment):
    async with AsyncSessionLocal() as db:
        return await PaymentService(db).approve(payment_id=payment.id,admin_telegram_id=r.users[0].telegram_id)


def attempt(r, user=1, *, paid=False, kind='portrait'):
    payload=ToolSubmit(telegram_id=r.users[user].telegram_id,request_id=uuid4(),kind=kind,use_paid_credit=paid)
    path=folder(payload.telegram_id,payload.request_id);path.mkdir(parents=True);(path/'input').write_bytes(b'test')
    return payload


async def admit(payload):
    async with AsyncSessionLocal() as db:
        try:return await submit(db,payload)
        except MediaToolError as exc:return exc.code


@pytest.mark.asyncio
@pytest.mark.parametrize('user',[1,2])
async def test_manual_purchase_snapshot_approval_once_and_no_subscription_change(records,user):
    r=records
    checkout=await order(r,user)
    async with AsyncSessionLocal() as db:
        await ApplicationSettingsService(db).set_value(key='portrait.price_'+('usdt' if user==2 else 'irt'),
            category='payments',value='999',is_sensitive=False,actor_user_id=r.users[0].id,actor_telegram_id=r.users[0].telegram_id)
        await db.commit()
    payment=await receipt(r,checkout,user)
    assert payment.amount==Decimal('1.25' if user==2 else '25000') and payment.plan_id is None
    assert PaymentResponse.model_validate(payment).plan_id is None
    async with AsyncSessionLocal() as db:
        assert await balance(db,r.users[user].telegram_id)==0
    outcomes=await asyncio.wait_for(asyncio.gather(approve(r,payment),approve(r,payment)),10)
    assert sorted(x.already_reviewed for x in outcomes)==[False,True]
    assert all(x.subscription is None for x in outcomes)
    async with AsyncSessionLocal() as db:
        assert await balance(db,r.users[user].telegram_id)==1
        assert await db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id==r.users[user].id))==(1 if user==1 else 0)


@pytest.mark.asyncio
async def test_rejected_payment_cannot_grant_credit(records):
    r=records;p=await receipt(r,await order(r))
    async with AsyncSessionLocal() as db:
        await PaymentService(db).reject(payment_id=p.id,admin_telegram_id=r.users[0].telegram_id,reason='Invalid receipt')
    with pytest.raises(PaymentConflict):await approve(r,p)
    async with AsyncSessionLocal() as db:assert await balance(db,r.users[1].telegram_id)==0


@pytest.mark.asyncio
async def test_credit_purchase_is_atomic_and_replayed_without_double_debit(records):
    r=records
    async with AsyncSessionLocal() as db:
        await CreditService(db).change(r.users[1].telegram_id,CreditChange(actor_telegram_id=r.users[0].telegram_id,
            request_id=uuid4(),expected_version=0,currency='IRT',action='credit',amount='30000',reason='Fund purchase'))
    checkout=await order(r,method='credit')
    async def pay():
        async with AsyncSessionLocal() as db:return await CreditService(db).purchase(checkout.id,r.users[1].telegram_id)
    results=await asyncio.wait_for(asyncio.gather(pay(),pay()),10)
    assert sorted(x['replayed'] for x in results)==[False,True]
    assert all(Decimal(x['account']['balance'])==5000 for x in results)
    async with AsyncSessionLocal() as db:
        assert await balance(db,r.users[1].telegram_id)==1
        assert await db.scalar(select(func.count()).select_from(CreditEntry).where(CreditEntry.kind=='purchase'))==1


@pytest.mark.asyncio
async def test_single_credit_cannot_be_spent_twice_and_failed_result_restores_it(records):
    r=records;p=await receipt(r,await order(r));await approve(r,p)
    assert await admit(attempt(r))=='tool_plan_disabled'
    first,second=attempt(r,paid=True),attempt(r,paid=True)
    outcomes=await asyncio.wait_for(asyncio.gather(admit(first),admit(second)),10)
    assert sum(isinstance(x,tuple) for x in outcomes)==1
    assert 'portrait_credit_missing' in outcomes
    row=next(x[0] for x in outcomes if isinstance(x,tuple))
    async with AsyncSessionLocal() as db:
        stored=await db.get(MediaToolRequest,row.id);stored.status='failed'
        await db.run_sync(lambda sync:refund_failed_credit(sync,stored));await db.commit()
        assert await balance(db,r.users[1].telegram_id)==1
    retried=await admit(attempt(r,paid=True));assert isinstance(retried,tuple)
    async with AsyncSessionLocal() as db:
        stored=await db.get(MediaToolRequest,retried[0].id);stored.status='uncertain'
        await db.run_sync(lambda sync:refund_failed_credit(sync,stored));await db.commit()
        assert await balance(db,r.users[1].telegram_id)==0


@pytest.mark.asyncio
async def test_weekly_plan_quota_reset_current_edits_and_kind_isolation(records):
    r=records
    first=await admit(attempt(r,kind='music'));assert isinstance(first,tuple)
    async with AsyncSessionLocal() as db:
        row=await db.get(MediaToolRequest,first[0].id);row.status='failed';await db.commit()
    assert await admit(attempt(r,kind='music'))=='tool_weekly_limit'
    assert await admit(attempt(r))=='tool_plan_disabled'
    async with AsyncSessionLocal() as db:
        row=await db.get(MediaToolRequest,first[0].id)
        row.created_at=quota_window_start_utc('weekly',timezone_name='Asia/Tehran')-timedelta(seconds=1)
        await db.commit()
    second=await admit(attempt(r,kind='music'));assert isinstance(second,tuple)
    async with AsyncSessionLocal() as db:
        row=await db.get(MediaToolRequest,second[0].id);row.status='failed'
        plan=await db.get(Plan,r.plan.id);plan.tool_limits={**plan.tool_limits,'music':{'limit':-1,'period':'weekly'}}
        await db.commit()
    assert isinstance(await admit(attempt(r,kind='music')),tuple)


@pytest.mark.asyncio
async def test_failed_credit_activation_rolls_back_payment_and_balance(records,monkeypatch):
    r=records
    async with AsyncSessionLocal() as db:
        await CreditService(db).change(r.users[1].telegram_id,CreditChange(actor_telegram_id=r.users[0].telegram_id,
            request_id=uuid4(),expected_version=0,currency='IRT',action='credit',amount='30000',reason='Fund purchase'))
    checkout=await order(r,method='credit')
    original=PaymentService._activate
    async def fail_after_activation(self,*args,**kwargs):
        await original(self,*args,**kwargs)
        raise RuntimeError('Simulated transaction interruption')
    monkeypatch.setattr(PaymentService,'_activate',fail_after_activation)
    with pytest.raises(RuntimeError):
        async with AsyncSessionLocal() as db:
            await CreditService(db).purchase(checkout.id,r.users[1].telegram_id)
    async with AsyncSessionLocal() as db:
        assert await balance(db,r.users[1].telegram_id)==0
        assert Decimal((await CreditService(db).profile(r.users[1].telegram_id,'IRT'))['balance'])==30000
        assert (await db.get(PaymentOrder,checkout.id)).status=='open'
        assert await db.scalar(select(func.count()).select_from(Payment).where(Payment.order_id==checkout.id))==0


@pytest.mark.asyncio
async def test_purchase_does_not_leak_entitlement_to_other_user(records):
    r=records;p=await receipt(r,await order(r));await approve(r,p)
    assert await admit(attempt(r,user=2,paid=True))=='portrait_credit_missing'
    async with AsyncSessionLocal() as db:assert await balance(db,r.users[1].telegram_id)==1
