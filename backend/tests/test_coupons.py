"""Discount pricing, durable reservations and financial races in disposable PostgreSQL."""
import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app.api.coupons import router
from app.api.payments import router as payments_router
from app.api.reporting import finance_csv
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.admin import AdminAccount
from app.models.coupon import Coupon, CouponAction, CouponUse
from app.models.credit import CreditEntry
from app.models.payment import Payment, PaymentStatus
from app.models.payment_destination import PaymentCard, UsdtDestination
from app.models.payment_order import PaymentOrder
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.user import User, UserStatus
from app.schemas.coupon import CouponChange, CouponQuote, CouponTerms
from app.schemas.credit import CreditChange
from app.schemas.payment import PaymentCreate, PaymentOrderCreate
from app.services.admin_access import AdminAccessDenied
from app.services.coupons import CouponError, CouponService, coupon_price
from app.services.credit import CreditError, CreditService
from app.services.payment import PaymentService
from app.services.payment_orders import PaymentOrderError, PaymentOrderService


def terms(**changes):
    return CouponTerms(**{"code": "SAVE20", "currency": "IRT", "kind": "percent", "value": "20", **changes})


@pytest.mark.parametrize("changes", [
    {"value": "NaN"}, {"value": "Infinity"}, {"value": 1.5}, {"value": True}, {"value": "0"},
    {"value": "-1"}, {"value": "100"}, {"value": "1.00001"}, {"max_discount": "0"},
    {"kind": "fixed", "value": "1.5"}, {"max_discount": "1.5"}, {"total_limit": 0},
    {"per_user_limit": -1}, {"code": "a"}, {"code": "<SCRIPT>"}, {"currency": "USD"},
    {"duration_days": [0]}, {"plan_ids": [-1]}, {"starts_at": "2026-10-01T12:00:00"},
    {"starts_at": "2026-10-02T12:00:00Z", "expires_at": "2026-10-01T12:00:00Z"},
])
def test_invalid_terms_are_rejected(changes):
    with pytest.raises(ValidationError):
        terms(**changes)


@pytest.mark.parametrize("currency,kind,value,cap,amount,final,discount", [
    ("IRT", "percent", "12.5", None, "101", "89", "12"),
    ("IRT", "fixed", "25", None, "101.1", "77", "25"),
    ("IRT", "percent", "30", "10", "101", "91", "10"),
    ("USDT", "percent", "20", None, "1.2345", "0.9876", "0.2469"),
    ("USDT", "percent", "12.5", None, "1.2345", "1.0802", "0.1543"),
    ("USDT", "fixed", "0.1234", "0.1", "1.2345", "1.1345", "0.1000"),
])
def test_exact_rounding_never_gives_more_than_the_coupon(currency, kind, value, cap, amount, final, discount):
    coupon = Coupon(id=uuid4(), version=1, **terms(currency=currency, kind=kind, value=value, max_discount=cap).model_dump())
    result = coupon_price(coupon, Decimal(amount))
    assert Decimal(result["final_price"]) == Decimal(final)
    assert Decimal(result["discount_amount"]) == Decimal(discount)
    assert Decimal(result["original_price"]) == Decimal(final) + Decimal(discount)


@pytest.mark.parametrize("value,amount,error", [("100", "100", "coupon_zero_total"), ("101", "100", "coupon_zero_total")])
def test_fixed_discount_cannot_create_a_free_checkout(value, amount, error):
    coupon = Coupon(id=uuid4(), version=1, **terms(kind="fixed", value=value).model_dump())
    with pytest.raises(CouponError, match=error):
        coupon_price(coupon, Decimal(amount))


def test_code_normalization_and_subunit_discount():
    assert terms(code=" welcome۲۰ ").code == "WELCOME20"
    assert PaymentOrderCreate(telegram_id=42, offer_code="plan", coupon_code=" save۲۰ ", currency="IRT").coupon_code == "SAVE20"
    coupon = Coupon(id=uuid4(), version=1, **terms(value="0.1").model_dump())
    with pytest.raises(CouponError, match="coupon_no_discount"):
        coupon_price(coupon, Decimal("100"))


@pytest_asyncio.fixture
async def records():
    assert settings.app_env == "test", "Requires APP_ENV=test and a disposable database"
    suffix = uuid4().hex
    async with AsyncSessionLocal() as db:
        base = 8_700_000_000_000 + uuid4().int % 100_000_000
        users = [User(telegram_id=base+i, first_name="Coupon test", status=UserStatus.ACTIVE,
                      preferred_language="en" if i == 2 else "fa") for i in range(4)]
        plan = Plan(name="پلن تخفیف", name_en="Coupon plan", slug="coupon_"+suffix,
            price=Decimal("100"), price_usdt=Decimal("1.2345"), duration_days=30, daily_download_limit=3,
            max_file_size_mb=300, max_quality=720, max_concurrent_downloads=1, is_system=False, is_active=True)
        card = PaymentCard(label="Coupon test", card_holder="Test", is_active=True,
                           card_number=str(10**15 + uuid4().int % (9*10**15)))
        destination = UsdtDestination(label="Coupon test", network_name="TRON", network_code="TRC20",
                                      address="T"+suffix, is_active=True)
        db.add_all(users+[plan, card, destination])
        await db.flush()
        db.add(AdminAccount(user_id=users[0].id, is_active=True, is_superadmin=True, created_by_user_id=users[0].id))
        await db.commit()
    try:
        yield SimpleNamespace(users=users, admin=users[0], plan=plan, card=card, destination=destination,
                              code="TEST"+suffix[:16].upper())
    finally:
        async with AsyncSessionLocal() as db:
            ids = [u.id for u in users]
            await db.execute(delete(CouponUse).where(CouponUse.user_id.in_(ids)))
            coupon_ids = select(Coupon.id).where(Coupon.code.like("TEST"+suffix[:16].upper()+"%"))
            await db.execute(delete(CouponAction).where(CouponAction.coupon_id.in_(coupon_ids)))
            await db.execute(delete(Coupon).where(Coupon.id.in_(coupon_ids)))
            # Production ledger is append-only; only disposable tests may truncate it.
            await db.execute(text("TRUNCATE credit_notices, credit_entries, credit_accounts"))
            await db.execute(delete(Payment).where(Payment.user_id.in_(ids)))
            await db.execute(delete(PaymentOrder).where(PaymentOrder.user_id.in_(ids)))
            await db.execute(delete(Subscription).where(Subscription.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(delete(Plan).where(Plan.id == plan.id))
            await db.execute(delete(PaymentCard).where(PaymentCard.id == card.id))
            await db.execute(delete(UsdtDestination).where(UsdtDestination.id == destination.id))
            await db.commit()
        await engine.dispose()


def change_data(r, **changes):
    return CouponChange(actor_telegram_id=r.admin.telegram_id, request_id=uuid4(), expected_version=0,
                        reason="Coupon regression test", terms=terms(code=r.code, **changes))


async def create(r, **changes):
    async with AsyncSessionLocal() as db:
        result = await CouponService(db).change(change_data(r, **changes))
        await db.commit()
        return result


async def edit(r, coupon, **changes):
    data = CouponChange(actor_telegram_id=r.admin.telegram_id, request_id=uuid4(), expected_version=coupon["version"],
        reason="Update coupon terms", terms={**coupon["terms"], **changes})
    async with AsyncSessionLocal() as db:
        result = await CouponService(db).change(data, UUID(coupon["id"]))
        await db.commit()
        return result


async def start(r, user=1, method="credit", code=None):
    currency = "USDT" if user == 2 else "IRT"
    async with AsyncSessionLocal() as db:
        return await PaymentOrderService(db).start(PaymentOrderCreate(telegram_id=r.users[user].telegram_id,
            offer_code=r.plan.slug, currency=currency, method=method, coupon_code=code or r.code,
            usdt_destination_id=r.destination.id if method == "manual" and currency == "USDT" else None))


async def submit(r, order, user=1):
    payload = dict(telegram_id=r.users[user].telegram_id, offer_code=order.offer_snapshot["code"],
                   currency=order.currency, order_id=order.id)
    if order.currency == "USDT":
        payload.update(usdt_destination_id=order.destination_snapshot["id"], txid=uuid4().hex*2)
    else:
        payload.update(payment_card_id=order.destination_snapshot["id"], receipt_file_id=uuid4().hex,
                       receipt_file_unique_id=uuid4().hex, receipt_file_type="photo")
    async with AsyncSessionLocal() as db:
        return (await PaymentService(db).create_payment(PaymentCreate(**payload))).payment


async def funds(r, user=1, amount="100"):
    async with AsyncSessionLocal() as db:
        await CreditService(db).change(r.users[user].telegram_id, CreditChange(actor_telegram_id=r.admin.telegram_id,
            request_id=uuid4(), expected_version=0, currency="USDT" if user == 2 else "IRT", action="credit",
            amount=amount, reason="Coupon purchase test"))


async def pay(r, order, user=1):
    async with AsyncSessionLocal() as db:
        return await CreditService(db).purchase(order.id, r.users[user].telegram_id)


@pytest.mark.asyncio
async def test_admin_permissions_idempotency_stale_edit_and_currency_identity(records):
    r = records
    data = change_data(r)
    async def attempt():
        async with AsyncSessionLocal() as db:
            result = await CouponService(db).change(data)
            await db.commit()
            return result
    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), 15)
    assert results[0]["id"] == results[1]["id"] and results[0]["version"] == 1
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(CouponAction).where(CouponAction.id == data.request_id)) == 1
        with pytest.raises(CouponError, match="coupon_request_reused"):
            await CouponService(db).change(data.model_copy(update={"reason": "Different reason"}))
    async with AsyncSessionLocal() as db:
        with pytest.raises(AdminAccessDenied):
            await CouponService(db).change(data.model_copy(update={"actor_telegram_id": r.users[1].telegram_id}))
    updated = await edit(r, results[0], value="30")
    assert updated["version"] == 2
    with pytest.raises(CouponError, match="coupon_stale"):
        await edit(r, results[0], active=False)
    with pytest.raises(CouponError, match="coupon_identity_immutable"):
        await edit(r, updated, currency="USDT")
    with pytest.raises(IntegrityError):
        await create(r)
    assert (await create(r, currency="USDT"))["id"] != updated["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,error", [
    ({"active": False}, "coupon_inactive"),
    ({"starts_at": datetime.now(timezone.utc)+timedelta(days=2)}, "coupon_not_started"),
    ({"expires_at": datetime.now(timezone.utc)-timedelta(days=2)}, "coupon_expired"),
    ({"duration_days": [90]}, "coupon_duration_scope"),
    ({"currency": "USDT"}, "coupon_not_found"),
])
async def test_new_orders_enforce_terms(records, changes, error):
    await create(records, **changes)
    with pytest.raises(CouponError, match=error):
        await start(records)


@pytest.mark.asyncio
async def test_plan_scope_and_quote_do_not_reserve_capacity(records):
    r = records
    coupon = await create(r, plan_ids=[r.plan.id], total_limit=1)
    async with AsyncSessionLocal() as db:
        data = CouponQuote(telegram_id=r.users[1].telegram_id, offer_code=r.plan.slug, currency="IRT", coupon_code=r.code)
        service = CouponService(db)
        assert (await service.quote(data))["final_price"] == "80"
        assert (await service.quote(data))["final_price"] == "80"
        assert await service.counts(UUID(coupon["id"])) == {}
        await db.commit()
    # A valid existing plan scope is enforced independently of the displayed quote.
    async with AsyncSessionLocal() as db:
        row = await db.get(Coupon, UUID(coupon["id"]))
        row.plan_ids = [r.plan.id + 1000000]
        await db.commit()
    with pytest.raises(CouponError, match="coupon_plan_scope"):
        await start(r)
    with pytest.raises(CouponError, match="coupon_plan_scope"):
        await edit(r, coupon, plan_ids=[r.plan.id+1000000])


@pytest.mark.asyncio
async def test_last_slot_is_reserved_once_and_cancellation_releases_it(records):
    r = records
    coupon = await create(r, total_limit=1)
    async def attempt(user):
        try:
            return user, await start(r, user)
        except CouponError as exc:
            return user, exc.code
    results = await asyncio.wait_for(asyncio.gather(attempt(1), attempt(3)), 15)
    assert len([v for _, v in results if v == "coupon_total_limit"]) == 1
    user, order = next((u, v) for u, v in results if isinstance(v, PaymentOrder))
    replays = await asyncio.wait_for(asyncio.gather(start(r, user), start(r, user)), 15)
    assert all(o.id == order.id for o in replays)
    async with AsyncSessionLocal() as db:
        service = PaymentOrderService(db)
        with pytest.raises(PaymentOrderError, match="payment_order_not_found"):
            await service.cancel(order.id, r.users[2].telegram_id)
        await db.rollback()
        await service.cancel(order.id, r.users[user].telegram_id)
        await service.cancel(order.id, r.users[user].telegram_id)
        assert await CouponService(db).counts(UUID(coupon["id"])) == {"released": 1}
    assert (await start(r, 3 if user == 1 else 1)).id != order.id


@pytest.mark.asyncio
@pytest.mark.parametrize("user,currency", [(1, "IRT"), (2, "USDT")])
@pytest.mark.parametrize("approved", [True, False])
async def test_manual_receipt_reserves_until_review_and_review_is_idempotent(records, user, currency, approved):
    r = records
    coupon = await create(r, currency=currency, total_limit=1)
    order = await start(r, user, "manual")
    payment = await submit(r, order, user)
    async with AsyncSessionLocal() as db:
        use = await db.get(CouponUse, order.id)
        assert use.status == "reserved" and use.payment_id == payment.id
        assert payment.discount_snapshot == order.offer_snapshot["coupon"]
        service = PaymentService(db)
        for _ in range(2):
            if approved:
                await service.approve(payment_id=payment.id, admin_telegram_id=r.admin.telegram_id)
            else:
                await service.reject(payment_id=payment.id, admin_telegram_id=r.admin.telegram_id, reason="Receipt mismatch")
        detail = await CouponService(db).detail(UUID(coupon["id"]))
        assert detail["counts"] == {"redeemed" if approved else "released": 1}
        expected = Decimal(order.offer_snapshot["coupon"]["discount_amount"]) if approved else 0
        assert Decimal(detail["redeemed_discount"]) == expected
        assert Decimal(detail["redeemed_amount"]) == (payment.amount if approved else 0)
    if approved:
        with pytest.raises(CouponError, match="coupon_total_limit"):
            await start(r, user)
    else:
        assert (await start(r, user)).id != order.id


@pytest.mark.asyncio
async def test_existing_order_honors_coupon_and_plan_snapshot_after_admin_changes(records):
    r = records
    coupon = await create(r)
    order = await start(r, method="manual")
    await edit(r, coupon, active=False, value="5", expires_at=datetime.now(timezone.utc)-timedelta(days=1))
    async with AsyncSessionLocal() as db:
        plan = await db.get(Plan, r.plan.id)
        plan.price, plan.is_active = Decimal("200"), False
        await db.commit()
    assert (await start(r, method="manual")).id == order.id
    payment = await submit(r, order)
    async with AsyncSessionLocal() as db:
        await PaymentService(db).approve(payment_id=payment.id, admin_telegram_id=r.admin.telegram_id)
        assert payment.amount == 80 and payment.discount_snapshot["value"] == "20.0000"
        assert (await db.get(CouponUse, order.id)).status == "redeemed"


@pytest.mark.asyncio
@pytest.mark.parametrize("user,currency,funds_amount,total,remaining", [(1, "IRT", "100", "80", "20"), (2, "USDT", "2", "0.9876", "1.0124")])
async def test_credit_discount_debit_activation_and_redemption_are_exactly_once(records, user, currency, funds_amount, total, remaining):
    r = records
    coupon = await create(r, currency=currency)
    await funds(r, user, funds_amount)
    order = await start(r, user)
    results = await asyncio.wait_for(asyncio.gather(pay(r, order, user), pay(r, order, user)), 15)
    assert sorted(v["replayed"] for v in results) == [False, True]
    assert all(Decimal(v["account"]["balance"]) == Decimal(remaining) for v in results)
    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, results[0]["payment_id"])
        assert payment.amount == Decimal(total) and payment.status == PaymentStatus.APPROVED
        assert (await db.get(CouponUse, order.id)).status == "redeemed"
        assert await db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id == r.users[user].id)) == 1
        assert await db.scalar(select(func.count()).select_from(CreditEntry).where(CreditEntry.payment_id == payment.id)) == 1
        response = await finance_csv(actor_telegram_id=r.admin.telegram_id, period="all", start=None, end=None, db=db)
        row = next(v for v in csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))) if int(v["payment_id"]) == payment.id)
        assert row["coupon_code"] == r.code and Decimal(row["amount"]) == Decimal(total)
        assert Decimal(row["original_amount"]) - Decimal(row["discount_amount"]) == Decimal(total)
        assert Decimal((await CouponService(db).detail(UUID(coupon["id"])))["redeemed_amount"]) == Decimal(total)
    with pytest.raises(CouponError, match="coupon_user_limit"):
        await start(r, user)


@pytest.mark.asyncio
async def test_failed_credit_purchase_preserves_balance_and_reservation(records, monkeypatch):
    r = records
    await create(r)
    order = await start(r)
    with pytest.raises(CreditError, match="credit_insufficient"):
        await pay(r, order)
    await funds(r)
    original = CreditService.append
    async def fail(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError("Failure after subscription and coupon redemption")
    monkeypatch.setattr(CreditService, "append", fail)
    with pytest.raises(RuntimeError, match="after subscription"):
        await pay(r, order)
    async with AsyncSessionLocal() as db:
        assert (await db.get(CouponUse, order.id)).status == "reserved"
        assert (await db.get(PaymentOrder, order.id)).status == "open"
        assert Decimal((await CreditService(db).profile(r.users[1].telegram_id, "IRT"))["balance"]) == 100
        assert await db.scalar(select(func.count()).select_from(Payment).where(Payment.user_id == r.users[1].id)) == 0
        assert await db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id == r.users[1].id)) == 0


@pytest.mark.asyncio
async def test_caps_cannot_be_lowered_below_reserved_or_redeemed_usage(records):
    r = records
    coupon = await create(r, per_user_limit=None)
    await funds(r, amount="200")
    await pay(r, await start(r))
    await start(r)
    for limits in ({"total_limit": 1}, {"per_user_limit": 1}):
        with pytest.raises(CouponError, match="coupon_limit_below_usage"):
            await edit(r, coupon, **limits)
    assert (await edit(r, coupon, total_limit=2, per_user_limit=2))["version"] == 2
    with pytest.raises(PaymentOrderError, match="open_payment_order_exists"):
        await start(r, code="DIFFERENT")


@pytest.mark.asyncio
async def test_api_auth_quote_currency_and_saved_order_serialization(records, monkeypatch):
    r = records
    monkeypatch.setattr(settings, "bot_backend_api_key", "c"*64)
    app = FastAPI()
    app.include_router(payments_router)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        query = {"telegram_id": r.users[1].telegram_id, "offer_code": r.plan.slug, "currency": "IRT", "coupon_code": r.code}
        assert (await client.post("/payments/coupons/quote", json=query)).status_code == 401
        client.headers["X-Internal-API-Key"] = "c"*64
        assert (await client.get(f"/admin/coupons?actor_telegram_id={r.users[1].telegram_id}")).status_code == 403
        payload = change_data(r).model_dump(mode="json")
        assert (await client.post("/admin/coupons", json={**payload, "actor_telegram_id": r.users[1].telegram_id})).status_code == 403
        assert (await client.post("/admin/coupons", json=payload)).status_code == 200
        response = await client.post("/payments/coupons/quote", json=query)
        assert response.status_code == 200 and response.json()["final_price"] == "80"
        assert (await client.post("/payments/coupons/quote", json={**query, "currency": "USDT"})).status_code == 403
        assert (await client.post("/payments/coupons/quote", json={**query, "offer_code": "missing"})).status_code == 409
        response = await client.post("/payments/orders", json={**query, "method": "credit", "price": "1"})
        assert response.status_code == 200, response.text
        order = response.json()
        assert order["offer"]["price"] == "80" and order["offer"]["coupon"]["code"] == r.code
        again = await client.get(f"/payments/orders/{order['id']}?telegram_id={r.users[1].telegram_id}")
        assert again.json()["offer"]["coupon"] == order["offer"]["coupon"]
        assert (await client.get(f"/payments/orders/{order['id']}?telegram_id={r.users[3].telegram_id}")).status_code == 404
