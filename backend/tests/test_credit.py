"""Financial invariants against PostgreSQL; no real payments or Telegram calls."""
import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.api.credit import router
from app.api.reporting import currency_expression
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.admin import AdminAccount
from app.models.credit import CreditAccount, CreditEntry, CreditNotice
from app.models.payment import Payment, PaymentStatus
from app.models.payment_order import PaymentOrder
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.user import User, UserStatus
from app.schemas.credit import CreditChange, CreditNoticeAck
from app.schemas.payment import PaymentCreate, PaymentOrderCreate
from app.services.admin_access import AdminAccessDenied
from app.services.admin_statistics import AdminStatisticsService
from app.services.credit import CreditError, CreditService, now_utc, reconcile_credit_notices_after_restore
from app.services.payment import PaymentService
from app.services.payment_management import PaymentManagementService
from app.services.payment_orders import PaymentOrderError, PaymentOrderService


@pytest_asyncio.fixture
async def records():
    # Teardown truncates only the three new ledger tables in the disposable CI database.
    # Normal UPDATE/DELETE are deliberately forbidden by the production ledger trigger.
    assert settings.app_env == "test", "Credit integration tests require APP_ENV=test and a disposable database"
    async with AsyncSessionLocal() as db:
        base = 8_800_000_000_000 + uuid.uuid4().int % 100_000_000
        users = [User(telegram_id=base+i, first_name="Credit test", status=UserStatus.ACTIVE,
                      preferred_language="en" if i == 2 else "fa") for i in range(4)]
        plan = Plan(name="Credit plan", name_en="Credit plan", slug="credit_"+uuid.uuid4().hex,
            price=Decimal(100), price_usdt=Decimal("1.2345"), duration_days=30, daily_download_limit=3,
            max_file_size_mb=300, max_quality=720, max_concurrent_downloads=1, is_system=False, is_active=True)
        db.add_all(users+[plan])
        await db.flush()
        db.add(AdminAccount(user_id=users[0].id, is_active=True, is_superadmin=True, created_by_user_id=users[0].id))
        await db.commit()
    try:
        yield SimpleNamespace(users=users, admin=users[0], plan=plan)
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(text("TRUNCATE credit_notices, credit_entries, credit_accounts"))
            ids = [u.id for u in users]
            await db.execute(delete(Payment).where(Payment.user_id.in_(ids)))
            await db.execute(delete(PaymentOrder).where(PaymentOrder.user_id.in_(ids)))
            await db.execute(delete(Subscription).where(Subscription.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(delete(Plan).where(Plan.id == plan.id))
            await db.commit()
        await engine.dispose()


async def change(records, amount="100", *, user_index=1, currency="IRT", action="credit", **kwargs):
    async with AsyncSessionLocal() as db:
        service = CreditService(db)
        profile = await service.profile(records.users[user_index].telegram_id, currency)
        data = CreditChange(**{"actor_telegram_id": records.admin.telegram_id, "request_id": uuid.uuid4(),
            "currency": currency, "expected_version": profile["version"], "action": action,
            "amount": amount, "reason": "Credit regression adjustment", **kwargs})
        return await service.change(records.users[user_index].telegram_id, data)


async def quote(records, user_index=1):
    async with AsyncSessionLocal() as db:
        return await PaymentOrderService(db).start(PaymentOrderCreate(telegram_id=records.users[user_index].telegram_id,
            offer_code=records.plan.slug, currency="USDT" if user_index == 2 else "IRT", method="credit"))


async def pay(records, order, user_index=1):
    async with AsyncSessionLocal() as db:
        return await CreditService(db).purchase(order.id, records.users[user_index].telegram_id)


@pytest.mark.parametrize("amount,currency", [("NaN", "USDT"), ("Infinity", "USDT"), ("-1", "IRT"),
    ("0", "IRT"), ("1.5", "IRT"), ("1.00001", "USDT"), (1.25, "USDT"), (True, "IRT"),
    ("100000000000000", "IRT")])
def test_money_validation_rejects_floats_nonfinite_negative_and_excess_precision(amount, currency):
    with pytest.raises(ValidationError):
        CreditChange(actor_telegram_id=42, request_id=uuid.uuid4(), currency=currency, expected_version=0,
                     action="credit", amount=amount, reason="Testing exact amounts")


@pytest.mark.parametrize("currency,amount", [("IRT", "123456789"), ("USDT", "0.0001"), ("USDT", "10.1234")])
def test_money_validation_preserves_decimal_amounts(currency, amount):
    data = CreditChange(actor_telegram_id=42, request_id=uuid.uuid4(), currency=currency, expected_version=0,
                        action="credit", amount=amount, reason="Testing exact amounts")
    assert data.amount == Decimal(amount)


@pytest.mark.asyncio
async def test_currency_isolated_and_customer_history_hides_internal_reason(records):
    await change(records, "100")
    await change(records, "0.1234", currency="USDT")
    async with AsyncSessionLocal() as db:
        service = CreditService(db)
        for currency, expected in (("IRT", "100"), ("USDT", "0.1234")):
            view = await service.profile(records.users[1].telegram_id, currency)
            assert Decimal(view["balance"]) == Decimal(expected)
            assert all("reason" not in entry and "actor" not in entry for entry in view["items"])
            admin = await service.profile(records.users[1].telegram_id, currency, admin=True)
            assert admin["items"][0]["reason"] == "Credit regression adjustment"


@pytest.mark.asyncio
async def test_duplicate_concurrent_adjustment_is_applied_once(records):
    request_id = uuid.uuid4()
    async def attempt():
        return await change(records, request_id=request_id, expected_version=0)
    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), 10)
    assert sorted(r["replayed"] for r in results) == [False, True]
    assert all(Decimal(r["account"]["balance"]) == 100 for r in results)
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(CreditEntry)) == 1
        assert await db.scalar(select(func.count()).select_from(CreditNotice)) == 1
    with pytest.raises(CreditError, match="credit_request_reused"):
        await change(records, "200", request_id=request_id, expected_version=0)


@pytest.mark.asyncio
async def test_concurrent_debits_cannot_overspend_and_stale_preview_is_rejected(records):
    await change(records)
    async def attempt():
        try:
            return await change(records, "80", action="debit", expected_version=1)
        except CreditError as exc:
            return exc.code
    outcomes = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), 10)
    assert len([x for x in outcomes if isinstance(x, dict)]) == 1
    assert "credit_stale_balance" in outcomes
    with pytest.raises(CreditError, match="credit_insufficient"):
        await change(records, "21", action="debit")
    async with AsyncSessionLocal() as db:
        assert (await CreditService(db).profile(records.users[1].telegram_id, "IRT"))["balance"] == "20.0000"


@pytest.mark.asyncio
async def test_append_only_ledger_and_nonnegative_account_are_database_enforced(records):
    result = await change(records)
    async with AsyncSessionLocal() as db:
        for statement in ("UPDATE credit_entries SET delta=9", "DELETE FROM credit_entries",
                          "UPDATE credit_accounts SET balance=-1", "UPDATE credit_accounts SET balance=1.5"):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text(statement))
        entry = await db.get(CreditEntry, uuid.UUID(result["entry_id"]))
        assert entry.delta == entry.balance_after == 100


@pytest.mark.asyncio
async def test_reversal_is_new_entry_once_and_cannot_make_balance_negative(records):
    original = await change(records)
    debit = await change(records, "20", action="debit")
    with pytest.raises(CreditError, match="credit_insufficient"):
        await change(records, None, action="reverse", entry_id=original["entry_id"])
    reversed_debit = await change(records, None, action="reverse", entry_id=debit["entry_id"])
    assert Decimal(reversed_debit["account"]["balance"]) == 100
    with pytest.raises(CreditError, match="credit_already_reversed"):
        await change(records, None, action="reverse", entry_id=debit["entry_id"])
    await change(records, None, action="reverse", entry_id=original["entry_id"])
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(CreditEntry)) == 4
        assert (await db.get(CreditEntry, uuid.UUID(original["entry_id"]))).delta == 100
        assert await db.scalar(select(func.sum(CreditEntry.delta))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("user_index,currency,funds,cost", [(1, "IRT", "300", "100"), (2, "USDT", "3.0000", "1.2345")])
async def test_concurrent_payment_debits_and_activates_exactly_once(records, user_index, currency, funds, cost):
    await change(records, funds, user_index=user_index, currency=currency)
    order = await quote(records, user_index)
    results = await asyncio.wait_for(asyncio.gather(pay(records, order, user_index), pay(records, order, user_index)), 10)
    assert sorted(r["replayed"] for r in results) == [False, True]
    expected = Decimal(funds)-Decimal(cost)
    async with AsyncSessionLocal() as db:
        service = CreditService(db)
        assert Decimal((await service.profile(records.users[user_index].telegram_id, currency))["balance"]) == expected
        payments = (await db.scalars(select(Payment).where(Payment.order_id == order.id))).all()
        assert len(payments) == 1 and payments[0].status == PaymentStatus.APPROVED
        assert payments[0].payment_method == ("credit_usdt" if currency == "USDT" else "credit_irt")
        assert payments[0].reviewed_by_telegram_id is None
        assert await db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id == records.users[user_index].id)) == 1
        assert await db.scalar(select(func.count()).select_from(CreditNotice)) == 3
        assert await db.scalar(select(currency_expression()).where(Payment.id == payments[0].id)) == currency
    with pytest.raises(CreditError, match="credit_invalid_reversal"):
        await change(records, None, user_index=user_index, currency=currency, action="reverse", entry_id=order.id)


@pytest.mark.asyncio
async def test_checkout_snapshot_survives_price_plan_and_limits_changes(records):
    await change(records, "500")
    order = await quote(records)
    async with AsyncSessionLocal() as db:
        plan = await db.get(Plan, records.plan.id)
        plan.price, plan.duration_days, plan.daily_download_limit, plan.is_active = 999, 365, 99, False
        await db.commit()
    result = await pay(records, order)
    assert Decimal(result["account"]["balance"]) == 400
    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, result["payment_id"])
        sub = await db.get(Subscription, payment.subscription_id)
        assert payment.amount == 100 and payment.duration_days == 30
        assert sub.expires_at - sub.started_at == timedelta(days=30)
        assert sub.daily_download_limit == 3


@pytest.mark.asyncio
async def test_same_plan_credit_renewal_uses_existing_subscription_rules(records):
    await change(records, "300")
    first = await pay(records, await quote(records))
    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, first["payment_id"])
        sub = await db.get(Subscription, payment.subscription_id)
        original_end = sub.expires_at
        subscription_id = sub.id
    second = await pay(records, await quote(records))
    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, second["payment_id"])
        sub = await db.get(Subscription, subscription_id)
        assert payment.subscription_id == sub.id and payment.subscription_change_type == "renewal"
        assert sub.expires_at == original_end + timedelta(days=30)
        assert sub.daily_download_limit == 6
        assert Decimal(second["account"]["balance"]) == 100


@pytest.mark.asyncio
async def test_insufficient_balance_and_failure_before_commit_leave_no_partial_purchase(records, monkeypatch):
    order = await quote(records)
    with pytest.raises(CreditError, match="credit_insufficient"):
        await pay(records, order)
    await change(records)
    original = CreditService.append
    async def fail_after_append(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError("Simulated failure before commit")
    monkeypatch.setattr(CreditService, "append", fail_after_append)
    with pytest.raises(RuntimeError, match="before commit"):
        await pay(records, order)
    async with AsyncSessionLocal() as db:
        assert (await db.get(PaymentOrder, order.id)).status == "open"
        assert await db.scalar(select(func.count()).select_from(Payment).where(Payment.user_id == records.users[1].id)) == 0
        assert await db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id == records.users[1].id)) == 0
        assert await db.scalar(select(func.sum(CreditEntry.delta))) == 100
        assert (await CreditService(db).profile(records.users[1].telegram_id, "IRT"))["balance"] == "100.0000"


@pytest.mark.asyncio
async def test_purchase_and_admin_debit_cannot_both_spend_the_same_credit(records):
    await change(records)
    order = await quote(records)
    async def debit():
        return await change(records, "80", action="debit", expected_version=1)
    results = await asyncio.wait_for(asyncio.gather(pay(records, order), debit(), return_exceptions=True), 10)
    assert len([r for r in results if isinstance(r, dict)]) == 1
    assert len([r for r in results if isinstance(r, CreditError)]) == 1
    async with AsyncSessionLocal() as db:
        balance = Decimal((await CreditService(db).profile(records.users[1].telegram_id, "IRT"))["balance"])
        assert balance in {Decimal(0), Decimal(20)}
        assert balance == await db.scalar(select(func.sum(CreditEntry.delta)))


@pytest.mark.asyncio
async def test_order_ownership_language_blocking_and_manual_receipt_bypass(records):
    await change(records)
    order = await quote(records)
    with pytest.raises(PaymentOrderError, match="payment_order_not_found"):
        await pay(records, order, 3)
    async with AsyncSessionLocal() as db:
        with pytest.raises(PaymentOrderError, match="payment_order_credit_required"):
            await PaymentService(db).create_payment(PaymentCreate(order_id=order.id, telegram_id=records.users[1].telegram_id,
                offer_code=records.plan.slug, receipt_file_id="test-file", receipt_file_type="photo", currency="IRT"))
    async with AsyncSessionLocal() as db:
        user = await db.get(User, records.users[1].id)
        user.preferred_language = "en"
        await db.commit()
    with pytest.raises(PaymentOrderError, match="payment_order_language"):
        await pay(records, order)
    async with AsyncSessionLocal() as db:
        user = await db.get(User, records.users[1].id)
        user.status = UserStatus.BLOCKED
        await db.commit()
    with pytest.raises(PermissionError):
        await pay(records, order)


@pytest.mark.asyncio
async def test_manual_checkout_cannot_spend_credit_and_cancelled_quote_cannot_pay(records):
    await change(records)
    order = await quote(records)
    async with AsyncSessionLocal() as db:
        stored = await db.get(PaymentOrder, order.id)
        stored.destination_snapshot = {"type": "card", "card_number": "test"}
        await db.commit()
    with pytest.raises(CreditError, match="credit_order_required"):
        await pay(records, order)
    async with AsyncSessionLocal() as db:
        stored = await db.get(PaymentOrder, order.id)
        stored.destination_snapshot = {"type": "credit"}
        await db.commit()
        await PaymentOrderService(db).cancel(order.id, records.users[1].telegram_id)
    with pytest.raises(CreditError, match="credit_order_closed"):
        await pay(records, order)


@pytest.mark.asyncio
async def test_notice_delivery_failure_and_recovery_never_change_money(records):
    await change(records)
    async with AsyncSessionLocal() as db:
        service = CreditService(db)
        notice = await service.claim_notice()
        await db.commit()
        assert notice["balance_after"] == "100.0000" and notice["kind"] == "user"
        assert await service.claim_notice() is None
        await service.ack_notice(notice["id"], CreditNoticeAck(claim_token=notice["claim_token"], status="uncertain"))
        await db.commit()
        assert await service.claim_notice() is None
        before = (await service.profile(records.users[1].telegram_id, "IRT"))["balance"]
    await pay(records, await quote(records))
    async with AsyncSessionLocal() as db:
        assert await reconcile_credit_notices_after_restore(db) == 2
        await db.commit()
        assert await CreditService(db).claim_notice() is None
        assert before == "100.0000"
        assert (await CreditService(db).profile(records.users[1].telegram_id, "IRT"))["balance"] == "0.0000"
        assert await db.scalar(select(func.sum(CreditEntry.delta))) == 0


@pytest.mark.asyncio
async def test_credit_payment_reports_keep_usdt_separate(records):
    async with AsyncSessionLocal() as db:
        old = (await PaymentManagementService(db).summary())["statistics"]["all"]
    await change(records, "2", user_index=2, currency="USDT")
    await pay(records, await quote(records, 2), 2)
    async with AsyncSessionLocal() as db:
        current = (await PaymentManagementService(db).summary())["statistics"]["all"]
        assert current["irt_total"] == old["irt_total"]
        assert current["usdt_total"] - old["usdt_total"] == Decimal("1.2345")
        totals = await AdminStatisticsService(db)._currency_finance(now_utc()-timedelta(minutes=5))
        assert Decimal(totals["USDT"]["total"]) >= Decimal("1.2345")


@pytest.mark.asyncio
async def test_api_key_permissions_customer_currency_and_no_secret_history(records, monkeypatch):
    monkeypatch.setattr(settings, "bot_backend_api_key", "c"*64)
    app = FastAPI()
    app.include_router(router)
    target = records.users[1].telegram_id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/credit/me?telegram_id={target}")).status_code == 401
        client.headers["X-Internal-API-Key"] = "c"*64
        assert (await client.get(f"/admin/credit/{target}?actor_telegram_id={target}")).status_code == 403
        payload = {"actor_telegram_id": target, "request_id": str(uuid.uuid4()), "currency": "IRT",
                   "expected_version": 0, "action": "credit", "amount": "100", "reason": "Internal reason only"}
        assert (await client.post(f"/admin/credit/{target}", json=payload)).status_code == 403
        payload["actor_telegram_id"] = records.admin.telegram_id
        assert (await client.post(f"/admin/credit/{target}", json=payload)).status_code == 200
        mine = (await client.get(f"/credit/me?telegram_id={target}")).json()
        assert mine["currency"] == "IRT" and mine["balance"] == "100.0000"
        assert "Internal reason only" not in str(mine)
        assert (await client.get(f"/credit/me?telegram_id={records.users[2].telegram_id}")).json()["currency"] == "USDT"
