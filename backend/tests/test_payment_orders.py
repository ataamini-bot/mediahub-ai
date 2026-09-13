"""Real PostgreSQL coverage for saved checkout terms and concurrent submission."""
import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token")

from app.db.session import AsyncSessionLocal, engine
from app.models.payment import Payment, PaymentStatus
from app.models.payment_order import PaymentOrder
from app.models.payment_destination import PaymentCard, UsdtDestination
from app.models.plan import Plan
from app.models.user import User, UserStatus
from app.schemas.payment import PaymentCreate, PaymentOrderCreate
from app.services.payment import PaymentService, DuplicateTxID
from app.services.payment_orders import PaymentOrderService, PaymentOrderError
from app.services.payment_management import PaymentDestinationValidation

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def records():
    suffix = uuid4().hex
    async with AsyncSessionLocal() as db:
        users = [User(telegram_id=8_900_000_000_000 + uuid4().int % 100_000_000,
                      first_name="Order test", preferred_language="en", status=UserStatus.ACTIVE) for _ in range(2)]
        plan = Plan(name="سفارش آزمایشی", name_en="Order plan", slug="order_"+suffix,
            price=Decimal("12000"), price_usdt=Decimal("3.1250"), duration_days=45,
            daily_download_limit=25, max_file_size_mb=800, max_quality=1080,
            max_concurrent_downloads=1, priority_processing=False, forced_join_required=False,
            is_unlimited=False, ai_enabled=False, is_system=False, is_active=True)
        destination = UsdtDestination(label="Order destination", network_name="TRON", network_code="TRC20",
                                      address="T"+suffix, is_active=True)
        cards = [PaymentCard(label="Order card", card_number=str(10**15 + uuid4().int % (9*10**15)),
                             card_holder="Order test", is_active=True) for _ in range(2)]
        db.add_all([*users, plan, destination, *cards])
        await db.commit()
    try:
        yield SimpleNamespace(users=users, plan=plan, destination=destination, cards=cards)
    finally:
        async with AsyncSessionLocal() as db:
            ids = [u.id for u in users]
            await db.execute(delete(Payment).where(Payment.user_id.in_(ids)))
            await db.execute(delete(PaymentOrder).where(PaymentOrder.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(delete(Plan).where(Plan.id == plan.id))
            await db.execute(delete(UsdtDestination).where(UsdtDestination.id == destination.id))
            await db.execute(delete(PaymentCard).where(PaymentCard.id.in_([c.id for c in cards])))
            await db.commit()
        await engine.dispose()


def start_data(r, user=0, currency="USDT"):
    return PaymentOrderCreate(telegram_id=r.users[user].telegram_id, offer_code=r.plan.slug, currency=currency,
                              usdt_destination_id=r.destination.id if currency == "USDT" else None)


def submission(r, order, user=0, txid=None):
    args = {"telegram_id": r.users[user].telegram_id, "offer_code": order.offer_snapshot["code"],
            "currency": order.currency, "order_id": order.id}
    if order.currency == "USDT":
        args.update(usdt_destination_id=order.destination_snapshot["id"], txid=txid or uuid4().hex*2)
    else:
        args.update(payment_card_id=order.destination_snapshot["id"], receipt_file_id=uuid4().hex,
                    receipt_file_type="photo", receipt_file_unique_id=uuid4().hex)
    return PaymentCreate(**args)


async def test_order_survives_price_duration_and_destination_changes(records):
    r = records
    async with AsyncSessionLocal() as db:
        service = PaymentOrderService(db)
        order = await service.start(start_data(r))
        plan = await db.get(Plan, r.plan.id)
        plan.price_usdt = Decimal("9")
        plan.duration_days = 90
        plan.daily_download_limit = 5
        plan.is_active = False
        destination = await db.get(UsdtDestination, r.destination.id)
        destination.address = "Tchanged"
        destination.is_active = False
        await db.commit()
        replay = await service.start(start_data(r))
        assert replay.id == order.id
        assert replay.offer_snapshot["price"] == "3.1250"
        assert replay.destination_snapshot["address"] == r.destination.address
        # Removing a wallet cannot erase instructions already given to a user.
        await db.delete(destination)
        await db.commit()
        result = await PaymentService(db).create_payment(submission(r, order))
        assert result.payment.amount == Decimal("3.1250")
        assert result.payment.duration_days == 45
        assert result.payment.plan_limits_snapshot["daily_download_limit"] == 25
        assert result.payment.payment_destination_snapshot["address"] == r.destination.address
        assert result.payment.receipt_file_id is None
        assert result.payment.order_id == order.id
        assert await service.current(r.users[0].telegram_id) is None


async def test_concurrent_checkout_rotates_card_only_once_and_new_order_rotates(records):
    r = records
    async with AsyncSessionLocal() as db:
        user = await db.get(User, r.users[0].id); user.preferred_language = "fa"
        await db.commit()
    async def start():
        async with AsyncSessionLocal() as db:
            return await PaymentOrderService(db).start(start_data(r, currency="IRT"))
    orders = await asyncio.wait_for(asyncio.gather(*(start() for _ in range(4))), timeout=15)
    assert len({o.id for o in orders}) == 1
    async with AsyncSessionLocal() as db:
        service = PaymentOrderService(db)
        assert (await db.execute(select(func.sum(PaymentCard.selection_count)).where(
            PaymentCard.id.in_([c.id for c in r.cards])))).scalar_one() == 1
        assert (await db.execute(select(func.count(Payment.id)).where(Payment.user_id == r.users[0].id))).scalar_one() == 0
        await service.cancel(orders[0].id, r.users[0].telegram_id)
        second = await service.start(start_data(r, currency="IRT"))
        assert second.id != orders[0].id
        assert second.destination_snapshot["id"] != orders[0].destination_snapshot["id"]
        assert (await db.execute(select(func.sum(PaymentCard.selection_count)).where(
            PaymentCard.id.in_([c.id for c in r.cards])))).scalar_one() == 2


async def test_card_submission_preserves_deleted_card_snapshot(records):
    r = records
    async with AsyncSessionLocal() as db:
        user = await db.get(User, r.users[0].id); user.preferred_language = "fa"
        await db.commit()
        order = await PaymentOrderService(db).start(start_data(r, currency="IRT"))
        data = submission(r, order)
        original_number = order.destination_snapshot["card_number"]
        await db.execute(delete(PaymentCard).where(PaymentCard.id == order.payment_card_id))
        plan = await db.get(Plan, r.plan.id); plan.price = Decimal("50000")
        await db.commit()
        result = await PaymentService(db).create_payment(data)
        assert result.payment.amount == Decimal("12000")
        assert result.payment.payment_card_id is None
        assert result.payment.payment_destination_snapshot["card_number"] == original_number


async def test_concurrent_submission_returns_one_payment_and_prevents_cancellation(records):
    r = records
    async with AsyncSessionLocal() as db:
        order = await PaymentOrderService(db).start(start_data(r))
    data = submission(r, order)
    async def submit():
        async with AsyncSessionLocal() as db:
            return await PaymentService(db).create_payment(data)
    results = await asyncio.wait_for(asyncio.gather(submit(), submit()), timeout=15)
    assert results[0].payment.id == results[1].payment.id
    assert sum(result.already_submitted for result in results) == 1
    async with AsyncSessionLocal() as db:
        with pytest.raises(PaymentOrderError, match="payment_order_submitted"):
            await PaymentOrderService(db).cancel(order.id, r.users[0].telegram_id)
        await db.rollback()
        with pytest.raises(PaymentOrderError, match="pending_payment_exists"):
            await PaymentOrderService(db).start(start_data(r))
        await db.rollback()
        assert (await db.get(PaymentOrder, order.id)).status == "submitted"


async def test_duplicate_txid_keeps_second_order_open(records):
    r = records
    async with AsyncSessionLocal() as db:
        first = await PaymentOrderService(db).start(start_data(r))
        second = await PaymentOrderService(db).start(start_data(r, user=1))
    txid = uuid4().hex * 2
    async def submit(order, user):
        async with AsyncSessionLocal() as db:
            try:
                return await PaymentService(db).create_payment(submission(r, order, user, txid))
            except DuplicateTxID:
                await db.rollback()
                return None
    results = await asyncio.wait_for(asyncio.gather(submit(first, 0), submit(second, 1)), timeout=15)
    assert sum(result is not None for result in results) == 1
    rejected_order = second if results[0] else first
    async with AsyncSessionLocal() as db:
        assert (await db.get(PaymentOrder, rejected_order.id)).status == "open"


async def test_order_ownership_cancellation_and_currency(records):
    r = records
    async with AsyncSessionLocal() as db:
        service = PaymentOrderService(db)
        order = await service.start(start_data(r))
        order_id = order.id
        own_data, other_data = submission(r, order), submission(r, order, user=1)
        with pytest.raises(PaymentOrderError, match="payment_order_not_found"):
            await service.get(order_id, r.users[1].id)
        with pytest.raises(PaymentOrderError, match="payment_order_not_found"):
            await service.cancel(order_id, r.users[1].telegram_id)
        await db.rollback()
        with pytest.raises(PaymentOrderError, match="payment_order_not_found"):
            await PaymentService(db).create_payment(other_data)
        await db.rollback()
        await service.cancel(order_id, r.users[0].telegram_id)
        with pytest.raises(PaymentOrderError, match="payment_order_closed"):
            await PaymentService(db).create_payment(own_data)
        await db.rollback()
        with pytest.raises(PaymentOrderError, match="payment_order_language"):
            await service.start(start_data(r, currency="IRT"))
        await db.rollback()


async def test_inactive_network_is_rejected_for_new_order(records):
    r = records
    async with AsyncSessionLocal() as db:
        destination = await db.get(UsdtDestination, r.destination.id)
        destination.is_active = False
        await db.commit()
        with pytest.raises(PaymentDestinationValidation):
            await PaymentOrderService(db).start(start_data(r))
        await db.rollback()
        assert await PaymentOrderService(db).current(r.users[0].telegram_id) is None
