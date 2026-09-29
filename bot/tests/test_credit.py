import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, User

from app.handlers import credit, payments
from app.keyboards.admin import build_admin_home_keyboard
from app.keyboards.payment import build_payment_offer_detail_keyboard
from app.middleware.interface import InterfaceRequests, ui_actor, ui_language, ui_state
from app.services import credit_notices
from app.services.backend import BackendAPIError


def message(**changes):
    return Message(**{"message_id": 10, "date": datetime.now(timezone.utc),
        "chat": Chat(id=42, type="private"), "from_user": User(id=42, is_bot=False, first_name="Test"), **changes})


def callback(data):
    return CallbackQuery(id="cb", from_user=User(id=42, is_bot=False, first_name="Test"), chat_instance="test",
                         data=data, message=message(from_user=User(id=999, is_bot=True, first_name="Bot")))


def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=999, chat_id=42, user_id=42))


def account(**changes):
    return {"telegram_id": 43, "name": "Customer", "language": "fa", "currency": "IRT", "balance": "100.0000",
            "version": 1, "items": [], "page": 1, "total": 0, **changes}


def order(**changes):
    return {"id": str(uuid.uuid4()), "status": "open", "currency": "IRT", "destination": {"type": "credit"},
            "offer": {"label": "Plan <literal>", "code": "custom", "duration_days": 30, "price": "80"}, **changes}


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=message()))
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(credit, "get_admin_context", AsyncMock(return_value={"is_admin": True, "permissions": ["balances.manage"]}))
    monkeypatch.setattr(credit, "get_telegram_user", AsyncMock(return_value={"effective_language": "fa"}))
    monkeypatch.setattr(credit, "_payment_request", AsyncMock(return_value=account()))


@pytest.mark.asyncio
async def test_admin_adjustment_requires_reason_preview_and_bound_final_confirmation(ui):
    fsm = state()
    await credit.adjust(callback("credit:adjust:debit:43:IRT"), fsm)
    await credit.receive_amount(message(text="۲۰"), fsm)
    await credit.receive_reason(message(text="Correction after checking account"), fsm)
    assert all(call.args[0] == "GET" for call in credit._payment_request.await_args_list)
    text = Message.answer.await_args.args[0]
    assert "100" in text and "80" in text and "-20" in text and "43" in text
    data = await fsm.get_data()
    assert data["expected_version"] == 1 and data["amount"] == "20"
    await credit.confirm_adjustment(callback("credit:admin:confirm"), fsm)
    posts = [call for call in credit._payment_request.await_args_list if call.args[0] == "POST"]
    assert len(posts) == 1
    assert posts[0].args == ("POST", "/admin/credit/43")
    assert posts[0].kwargs["payload"] == {"actor_telegram_id": 42, "currency": "IRT",
        "request_id": data["request_id"], "expected_version": 1, "action": "debit", "reason": "Correction after checking account", "amount": "20"}


@pytest.mark.asyncio
@pytest.mark.parametrize("value,currency", [("NaN", "USDT"), ("Infinity", "IRT"), ("-5", "IRT"),
    ("0", "USDT"), ("0.5", "IRT"), ("1.00001", "USDT"), ("100000000000000", "IRT")])
async def test_invalid_amount_never_advances_to_confirmation(ui, value, currency):
    fsm = state()
    await fsm.set_state(credit.CreditStates.amount)
    await fsm.update_data(credit_currency=currency, action="credit")
    await credit.receive_amount(message(text=value), fsm)
    assert await fsm.get_state() == credit.CreditStates.amount.state
    assert "amount" not in await fsm.get_data()
    credit._payment_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_permissions_and_private_chat_prevent_credit_changes(ui):
    assert not await credit.admin_allowed(message(chat=Chat(id=-100, type="supergroup")))
    credit.get_admin_context.return_value = {"is_admin": True, "permissions": ["users.manage"]}
    fsm = state()
    await credit.adjust(callback("credit:adjust:credit:43:IRT"), fsm)
    await credit.confirm_adjustment(callback("credit:admin:confirm"), fsm)
    await credit.receive_amount(message(text="100"), fsm)
    credit._payment_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_reversal_uses_original_transaction_and_new_idempotency_key(ui):
    fsm = state()
    entry = {"id": str(uuid.uuid4()), "kind": "adjustment", "reversed": False, "delta": "-10"}
    await fsm.update_data(credit_target=43, credit_currency="IRT", credit_account=account(items=[entry]))
    await credit.reverse(callback(f"credit:reverse:{entry['id']}"), fsm)
    data = await fsm.get_data()
    assert data["action"] == "reverse" and data["entry_id"] == entry["id"] and data["delta"] == "10"
    assert data["request_id"] != entry["id"] and "amount" not in data
    assert await fsm.get_state() == credit.CreditStates.reason.state


@pytest.mark.asyncio
async def test_credit_checkout_sends_only_offer_code_then_waits_for_explicit_payment(ui):
    fsm = state()
    await fsm.update_data(offer={"code": "custom", "price": "1", "duration_days": 999})
    saved_order = order()
    credit._payment_request.side_effect = [saved_order, account(), {"payment_id": 9, "account": account(balance="20")}]
    await credit.start_purchase(callback("credit:pay:start"), fsm)
    assert credit._payment_request.await_args_list[0].kwargs["payload"] == {
        "telegram_id": 42, "offer_code": "custom", "currency": "IRT", "method": "credit"}
    assert not any("/pay" == call.args[1][-4:] for call in credit._payment_request.await_args_list)
    assert "80" in Message.answer.await_args.args[0] and "999" not in Message.answer.await_args.args[0]
    assert "&lt;literal&gt;" in Message.answer.await_args.args[0]
    await credit.pay(callback("credit:pay:confirm"), fsm)
    assert credit._payment_request.await_args.args == ("POST", f"/credit/orders/{saved_order['id']}/pay")
    assert credit._payment_request.await_args.kwargs["payload"] == {"telegram_id": 42}
    assert await fsm.get_data() == {}


@pytest.mark.asyncio
async def test_insufficient_credit_has_cancel_and_change_but_no_pay_button(ui):
    credit._payment_request.return_value = account(balance="79")
    await credit.preview_purchase(message(), state(), 42, order())
    markup = Message.answer.await_args.kwargs["reply_markup"]
    values = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "credit:pay:confirm" not in values
    assert any(value.startswith("payment:order:cancel:") for value in values)
    assert any(value.startswith("payment:order:change:") for value in values)


@pytest.mark.asyncio
async def test_english_credit_flow_and_balance_do_not_display_toman(ui):
    token = ui_language.set("en")
    try:
        credit.get_telegram_user.return_value = {"effective_language": "en"}
        credit._payment_request.return_value = account(currency="USDT", balance="2.2345")
        await credit.preview_purchase(message(), state(), 42, order(currency="USDT", offer={
            "label": "Premium", "code": "premium", "price": "1.2345", "duration_days": 30}))
        text = Message.answer.await_args.args[0]
        assert "1.2345 USDT" in text and "IRT" not in text
        assert not any('\u0600' <= ch <= '\u06ff' for ch in text)
        await credit.show_mine(message(), 42)
        assert credit._payment_request.await_args.args[1] == "/credit/me?telegram_id=42&page=1"
    finally:
        ui_language.reset(token)


@pytest.mark.asyncio
async def test_resume_saved_credit_order_uses_real_callback_actor(ui, monkeypatch):
    saved_order = order()
    monkeypatch.setattr(credit, "preview_purchase", AsyncMock())
    await payments._show_payment_order(message(from_user=User(id=999, is_bot=True, first_name="Bot")), state(), saved_order, "fa", 42)
    assert credit.preview_purchase.await_args.args[2] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["credit:admin:confirm", "credit:pay:confirm"])
async def test_sensitive_buttons_use_actor_message_and_form_binding(action):
    fsm = state()
    await fsm.update_data(order_id=str(uuid.uuid4()), expected_version=3)
    tokens = ui_actor.set(42), ui_state.set(fsm)
    redis = SimpleNamespace(set=AsyncMock())
    try:
        send = AsyncMock(return_value=message())
        await InterfaceRequests(redis)(send, None, SendMessage(chat_id=42, text="Review",
            reply_markup=credit.keyboard([[('Confirm', action)]])))
        assert send.await_args.args[1].reply_markup.inline_keyboard[0][0].callback_data.startswith("gate:")
    finally:
        ui_actor.reset(tokens[0])
        ui_state.reset(tokens[1])


def test_menu_and_purchase_credit_actions_are_available_with_correct_permissions():
    def callbacks(markup):
        return {b.callback_data for row in markup.inline_keyboard for b in row}
    assert "credit:admin" in callbacks(build_admin_home_keyboard({"balances.manage"}, is_superadmin=False))
    assert "credit:admin" not in callbacks(build_admin_home_keyboard({"payments.review"}, is_superadmin=False))
    assert "credit:pay:start" in callbacks(build_payment_offer_detail_keyboard("en"))


def notice(**changes):
    return {"id": 1, "kind": "user", "entry_kind": "adjustment", "telegram_id": 43, "language": "en", "currency": "USDT",
            "delta": "1.2345", "balance_after": "2.2345", "claim_token": str(uuid.uuid4()),
            "payment_id": 17, "plan_name": "Premium", "duration_days": 30, **changes}


@pytest.mark.asyncio
async def test_notification_has_exact_balance_and_approval_report_has_no_receipt(monkeypatch):
    bot = SimpleNamespace(send_message=AsyncMock())
    assert await credit_notices.send_notice(bot, notice()) == {"status": "sent"}
    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == 43 and "2.2345 USDT" in sent["text"] and "request_timeout" in sent
    assert "message_thread_id" not in sent
    assert "USDT" not in credit_notices.user_text(notice(language="fa"))
    monkeypatch.setattr(credit_notices, "notification_routes", AsyncMock(return_value={
        "enabled": True, "chat_id": -100, "topics": {"payments": 55}}))
    await credit_notices.send_notice(bot, notice(kind="report", entry_kind="purchase", delta="-1.2345"))
    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == -100 and sent["message_thread_id"] == 55
    assert "1.2345 USDT" in sent["text"] and "reply_markup" not in sent and sent["parse_mode"] is None


@pytest.mark.asyncio
async def test_network_uncertainty_is_not_retried_as_a_payment_or_notification():
    method = SendMessage(chat_id=43, text="Notice")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TelegramNetworkError(method=method, message="Lost response")))
    assert await credit_notices.send_notice(bot, notice()) == {"status": "uncertain"}
    bot.send_message.side_effect = TelegramRetryAfter(method=method, message="Flood", retry_after=17)
    assert await credit_notices.send_notice(bot, notice()) == {"status": "retry", "retry_after": 17}


@pytest.mark.asyncio
async def test_notice_ack_failure_retries_acknowledgement_without_resending(monkeypatch):
    delivery, calls = notice(), []
    async def backend(method, path, **kwargs):
        calls.append(path)
        if path.endswith("/claim"):
            if len(calls) > 1:
                raise asyncio.CancelledError()
            return delivery
        if calls.count(path) == 1:
            raise BackendAPIError(status_code=503, detail="Lost response")
        return {"status": "sent"}
    monkeypatch.setattr(credit_notices, "_payment_request", backend)
    monkeypatch.setattr(credit_notices, "send_notice", AsyncMock(return_value={"status": "sent"}))
    monkeypatch.setattr(credit_notices.asyncio, "sleep", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await credit_notices.run_credit_notices(None)
    credit_notices.send_notice.assert_awaited_once()
    assert calls.count("/internal/credit/notices/1/ack") == 2
