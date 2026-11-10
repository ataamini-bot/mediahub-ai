import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message

from app.handlers import admin_finance, coupon_checkout, coupons, credit, payments
from app.keyboards.admin import build_admin_home_keyboard
from app.keyboards.payment import build_payment_offer_detail_keyboard
from app.middleware.interface import InterfaceRequests, ui_actor, ui_language, ui_state
from app.services.backend import BackendAPIError
from app.services.credit_notices import report_text
from app.state.payment import PaymentStates
from app.utils.coupons import coupon_error, discount_text
from tests.test_credit import callback, message, state


COUPON_ID = str(uuid4())
SNAPSHOT = {"coupon_id": COUPON_ID, "code": "SAVE20", "version": 1, "currency": "IRT", "kind": "percent",
            "value": "20", "original_price": "100", "discount_amount": "20", "final_price": "80"}
TERMS = {"code": "SAVE20", "currency": "IRT", "kind": "percent", "value": "20", "max_discount": None,
         "starts_at": None, "expires_at": None, "total_limit": 5, "per_user_limit": 1,
         "plan_ids": [7], "duration_days": [30], "active": True}


def offer(currency="IRT"):
    return {"code": "custom", "label": "Pro", "price": "100", "currency": currency, "duration_days": 30,
            "description": "Fast downloads", "daily_download_limit": 3, "max_file_size_mb": 300,
            "max_quality": 720, "max_concurrent_downloads": 1, "priority_processing": False,
            "forced_join_required": False}


def detail(**changes):
    return {"id": COUPON_ID, "terms": TERMS.copy(), "version": 1, "counts": {}, "recent": [],
            "plans": [{"id": 7, "name": "پلن", "name_en": "Plan"}], **changes}


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=message()))
    monkeypatch.setattr(Message, "edit_text", AsyncMock(return_value=message()))
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(coupons, "get_admin_context", AsyncMock(return_value={"is_admin": True, "permissions": ["coupons.manage"]}))
    async def api(method, path, **kwargs):
        if "/options" in path:
            return [{"id": 7, "name": "پلن", "name_en": "Plan", "duration_days": 30}]
        return detail()
    monkeypatch.setattr(coupons, "_payment_request", AsyncMock(side_effect=api))
    monkeypatch.setattr(coupon_checkout, "_payment_request", AsyncMock(return_value=SNAPSHOT.copy()))


@pytest.mark.asyncio
async def test_admin_wizard_requires_full_terms_reason_and_final_confirmation(ui):
    fsm = state()
    await coupons.new(callback("coupon:new"), fsm)
    await coupons.receive_field(message(text=" save۲۰ "), fsm)
    await coupons.choice(callback("coupon:choice:currency:IRT"), fsm)
    await coupons.choice(callback("coupon:choice:kind:percent"), fsm)
    for text in ("۲۰", "-", "-", "-", "۵", "۱"):
        await coupons.receive_field(message(text=text), fsm)
    await coupons.plans(callback("coupon:plan:7:0"), fsm)
    await coupons.plans(callback("coupon:plans:done"), fsm)
    await coupons.receive_field(message(text="۳۰"), fsm)
    assert await fsm.get_state() == coupons.CouponStates.reason.state
    await coupons.reason(message(text="Launch <promotion>"), fsm)
    assert await fsm.get_state() == coupons.CouponStates.confirm.state
    data = await fsm.get_data()
    assert data["coupon_terms"] == TERMS
    assert "&lt;promotion&gt;" in Message.answer.await_args.args[0]
    assert "پلن" in Message.answer.await_args.args[0]
    assert all(c.args[0] == "GET" for c in coupons._payment_request.await_args_list)
    await coupons.confirm(callback("coupon:confirm"), fsm)
    write = next(c for c in coupons._payment_request.await_args_list if c.args[0] == "POST")
    assert write.args == ("POST", "/admin/coupons")
    assert write.kwargs["payload"] == {"actor_telegram_id": 42, "request_id": data["coupon_request_id"],
        "expected_version": 0, "reason": "Launch <promotion>", "terms": TERMS}
    assert await fsm.get_state() is None


@pytest.mark.asyncio
async def test_coupon_writes_require_private_chat_and_permission_at_confirmation(ui):
    assert not await coupons.allowed(message(chat=Chat(id=-100, type="supergroup")))
    coupons.get_admin_context.return_value = {"is_admin": True, "permissions": ["payments.review"]}
    fsm = state()
    await coupons.new(callback("coupon:new"), fsm)
    await coupons.confirm(callback("coupon:confirm"), fsm)
    await coupons.receive_field(message(text="20"), fsm)
    coupons._payment_request.assert_not_awaited()
    assert "coupon:home:1" not in {b.callback_data for row in build_admin_home_keyboard({"payments.review"}, is_superadmin=False).inline_keyboard for b in row}
    assert "coupon:home:1" in {b.callback_data for row in build_admin_home_keyboard({"coupons.manage"}, is_superadmin=False).inline_keyboard for b in row}


@pytest.mark.asyncio
async def test_edit_preserves_identity_and_binds_final_confirmation_to_form_and_actor(ui):
    fsm = state()
    await coupons.edit(callback(f"coupon:edit:{COUPON_ID}"), fsm)
    callbacks = {b.callback_data for row in Message.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in row}
    assert "coupon:field:code" not in callbacks and "coupon:field:currency" not in callbacks
    await coupons.field(callback("coupon:field:active"), fsm)
    await coupons.choice(callback("coupon:choice:active:false"), fsm)
    await coupons.reason(message(text="Pause campaign"), fsm)
    tokens = ui_actor.set(42), ui_state.set(fsm)
    redis = SimpleNamespace(set=AsyncMock())
    try:
        send = AsyncMock(return_value=message())
        await InterfaceRequests(redis)(send, None, SendMessage(chat_id=42, text="Review",
            reply_markup=Message.answer.await_args.kwargs["reply_markup"]))
        assert send.await_args.args[1].reply_markup.inline_keyboard[0][0].callback_data.startswith("gate:")
    finally:
        ui_actor.reset(tokens[0]); ui_state.reset(tokens[1])
    await coupons.confirm(callback("coupon:confirm"), fsm)
    data = next(c for c in coupons._payment_request.await_args_list if c.args[0] == "PUT").kwargs["payload"]
    assert data["expected_version"] == 1 and data["terms"]["active"] is False
    assert data["terms"]["code"] == "SAVE20" and data["terms"]["currency"] == "IRT"


@pytest.mark.asyncio
async def test_lost_response_keeps_same_admin_request_id_for_retry(ui):
    fsm = state()
    await fsm.update_data(coupon_terms=TERMS.copy(), coupon_id=COUPON_ID, expected_version=1)
    await coupons.reason(message(text="Retry the same update"), fsm)
    coupons._payment_request.side_effect = BackendAPIError(status_code=503, detail="backend_unavailable")
    await coupons.confirm(callback("coupon:confirm"), fsm)
    first = coupons._payment_request.await_args.kwargs["payload"]
    assert await fsm.get_state() == coupons.CouponStates.confirm.state
    coupons._payment_request.side_effect = None
    coupons._payment_request.return_value = detail()
    await coupons.confirm(callback("coupon:confirm"), fsm)
    writes = [c for c in coupons._payment_request.await_args_list if c.args[0] == "PUT"]
    assert writes[-1].kwargs["payload"] == first


@pytest.mark.parametrize("name,value,changes", [
    ("value", "NaN", {}), ("value", "Infinity", {}), ("value", "100", {}), ("value", "۰", {}),
    ("value", "1.00001", {}), ("value", "1.5", {"kind": "fixed"}), ("max_discount", "1.5", {}),
    ("total_limit", "-1", {}), ("per_user_limit", "0", {}), ("duration_days", "30,0", {}),
    ("starts_at", "not-a-date", {}), ("code", "کد نامعتبر", {}),
])
def test_admin_input_rejects_invalid_money_dates_and_limits(name, value, changes):
    with pytest.raises(ValueError):
        coupons.parse_value(name, value, {**TERMS, **changes})


@pytest.mark.asyncio
async def test_invalid_expiry_does_not_mutate_the_saved_form(ui):
    fsm = state()
    assert coupons.parse_value("starts_at", "2026-10-01 18:30", TERMS) == "2026-10-01T15:00:00+00:00"
    original = {**TERMS, "starts_at": "2026-10-01T15:00:00+00:00", "expires_at": "2026-10-05T15:00:00+00:00"}
    await fsm.update_data(coupon_terms=original, coupon_id=COUPON_ID, field="expires_at")
    await fsm.set_state(coupons.CouponStates.field)
    await coupons.receive_field(message(text="2026-09-01 18:30"), fsm)
    assert (await fsm.get_data())["coupon_terms"] == original
    assert await fsm.get_state() == coupons.CouponStates.field.state


@pytest.mark.asyncio
@pytest.mark.parametrize("language,currency", [("fa", "IRT"), ("en", "USDT")])
async def test_customer_can_quote_and_remove_code_before_order(ui, language, currency):
    token = ui_language.set(language)
    try:
        snapshot = {**SNAPSHOT, "currency": currency}
        coupon_checkout._payment_request.return_value = snapshot
        fsm = state()
        await fsm.update_data(offer=offer(currency), offer_code="custom")
        await coupon_checkout.enter(callback("payment:coupon:enter"), fsm)
        await coupon_checkout.receive(message(text="save۲۰"), fsm)
        assert coupon_checkout._payment_request.await_args.kwargs["payload"] == {
            "telegram_id": 42, "offer_code": "custom", "currency": currency, "coupon_code": "SAVE20"}
        data = await fsm.get_data()
        assert data["offer"]["price"] == "80" and data["offer"]["coupon"] == snapshot
        assert "order_id" not in data
        assert await fsm.get_state() == PaymentStates.confirming_offer.state
        text = Message.answer.await_args.args[0]
        if language == "en":
            assert not re.search(r"[\u0600-\u06ff]", text)
        assert "SAVE20" in text
        assert Message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "payment:coupon:remove"
        await coupon_checkout.cancel(callback("payment:coupon:remove"), fsm)
        data = await fsm.get_data()
        assert data["coupon_code"] is None and data["offer"]["price"] == "100" and "coupon" not in data["offer"]
    finally:
        ui_language.reset(token)


@pytest.mark.asyncio
async def test_existing_order_cannot_change_coupon_and_invalid_code_does_not_apply(ui):
    fsm = state()
    await fsm.update_data(offer=offer(), order_id="saved-order", coupon_code="SAVE20")
    await coupon_checkout.enter(callback("payment:coupon:enter"), fsm)
    await coupon_checkout.cancel(callback("payment:coupon:remove"), fsm)
    assert (await fsm.get_data())["coupon_code"] == "SAVE20"
    coupon_checkout._payment_request.assert_not_awaited()
    await fsm.clear()
    await fsm.update_data(offer=offer())
    await coupon_checkout.enter(callback("payment:coupon:enter"), fsm)
    await coupon_checkout.receive(message(text="<invalid>"), fsm)
    coupon_checkout._payment_request.assert_not_awaited()
    coupon_checkout._payment_request.side_effect = BackendAPIError(status_code=409, detail={"code": "coupon_total_limit"})
    await coupon_checkout.receive(message(text="SAVE20"), fsm)
    assert "coupon_code" not in await fsm.get_data()
    assert await fsm.get_state() == PaymentStates.waiting_for_coupon.state


@pytest.mark.asyncio
@pytest.mark.parametrize("method,currency", [("manual", "IRT"), ("manual", "USDT"), ("credit", "IRT"), ("credit", "USDT")])
async def test_only_coupon_code_is_sent_when_creating_payment_or_credit_order(ui, monkeypatch, method, currency):
    fsm = state()
    await fsm.update_data(offer={**offer(currency), "price": "1"}, offer_code="custom", coupon_code="SAVE20")
    user = {"effective_language": "en" if currency == "USDT" else "fa"}
    if method == "credit":
        monkeypatch.setattr(credit, "get_telegram_user", AsyncMock(return_value=user))
        monkeypatch.setattr(credit, "_payment_request", AsyncMock(return_value={}))
        monkeypatch.setattr(credit, "preview_purchase", AsyncMock())
        await credit.start_purchase(callback("credit:pay:start"), fsm)
        sent = credit._payment_request.await_args.kwargs["payload"]
    else:
        monkeypatch.setattr(payments, "get_telegram_user", AsyncMock(return_value=user))
        monkeypatch.setattr(payments, "create_payment_order", AsyncMock(return_value={}))
        monkeypatch.setattr(payments, "_show_payment_order", AsyncMock())
        if currency == "USDT":
            await payments.select_usdt_destination(callback("payment:usdt-destination:7"), fsm)
        else:
            await payments.continue_payment_offer(callback("payment:continue"), fsm)
        sent = payments.create_payment_order.await_args.kwargs
    assert sent["telegram_id"] == 42 and sent["coupon_code"] == "SAVE20" and sent["currency"] == currency
    assert "price" not in sent and "discount" not in sent


@pytest.mark.asyncio
async def test_selecting_another_plan_clears_the_previous_quote(ui, monkeypatch):
    fsm = state()
    await fsm.update_data(coupon_code="SAVE20", coupon_base_offer=offer(), order_id="old-order")
    monkeypatch.setattr(payments, "get_telegram_user", AsyncMock(return_value={"effective_language": "fa"}))
    monkeypatch.setattr(payments, "get_payment_configuration", AsyncMock(return_value={"offers": [offer()]}))
    await payments.select_payment_offer(callback("payment:offer:custom"), fsm)
    data = await fsm.get_data()
    assert data["coupon_code"] is None and data["coupon_base_offer"] is None and data["order_id"] is None


def test_coupon_amounts_appear_in_private_review_and_approval_reports():
    payment = {"id": 1, "amount": "80", "status": "approved", "discount_snapshot": SNAPSHOT,
        "user_telegram_id": 42, "plan_name_snapshot": "Plan", "duration_days": 30, "payment_method": "card_to_card"}
    result = {"payment": payment, "user": {"telegram_id": 42}}
    for text in (payments._build_admin_caption(result, offer()), payments._approval_report(payment),
                 admin_finance._payment_caption(payment), report_text({"payment_id": 1, "telegram_id": 42,
                     "plan_name": "Plan", "duration_days": 30, "delta": "-80", "currency": "IRT", "discount_snapshot": SNAPSHOT})):
        assert all(value in text for value in ("SAVE20", "100", "20", "80"))
    en = discount_text({**SNAPSHOT, "currency": "USDT"}, "en")
    assert not re.search(r"[\u0600-\u06ff]", en)
    assert "capacity" in coupon_error(BackendAPIError(status_code=409, detail={"code": "coupon_total_limit"}), "en")
    assert build_payment_offer_detail_keyboard("en").inline_keyboard[0][0].callback_data == "payment:coupon:enter"
