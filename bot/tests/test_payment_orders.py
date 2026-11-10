import asyncio
import re
from copy import deepcopy
from unittest.mock import AsyncMock

from aiogram.types import Message, CallbackQuery

from app.handlers import payments
from app.keyboards.payment import build_usdt_screenshot_keyboard, format_usdt
from app.services.backend import BackendAPIError
from app.state.payment import PaymentStates
from tests.test_bilingual_payment_support import _payment_callback, _usdt_configuration

ORDER_ID = "36d081d1-05a1-4dc0-bf21-a3e583b0de5e"


def saved_order():
    config = _usdt_configuration()
    return {"id": ORDER_ID, "status": "open", "currency": "USDT", "offer": config["offers"][0],
            "destination": config["destinations"][1], "receipt": config["receipt"]}


def test_saved_purchase_restores_after_fsm_loss_without_loading_catalog(monkeypatch):
    callback, state = _payment_callback("payment:open", photo=True)
    order = saved_order()
    monkeypatch.setattr(payments, "get_telegram_user", AsyncMock(return_value={"effective_language": "en"}))
    monkeypatch.setattr(payments, "get_current_payment_order", AsyncMock(return_value=order))
    monkeypatch.setattr(payments, "get_payment_order", AsyncMock(return_value=order))
    catalog = AsyncMock(side_effect=AssertionError("Catalog must not be consulted"))
    monkeypatch.setattr(payments, "get_payment_configuration", catalog)
    answer = AsyncMock(); photo = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "answer_photo", photo)
    monkeypatch.setattr(Message, "delete", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    async def exercise():
        await payments.open_payment_offers(callback, state)
        resume, _ = _payment_callback(f"payment:order:resume:{ORDER_ID}")
        await payments.saved_payment_order_action(resume, state)
        assert (await state.get_data())["order_id"] == ORDER_ID
        assert await state.get_state() == PaymentStates.waiting_for_txid.state
    asyncio.run(exercise())
    assert "Unfinished purchase" in answer.await_args.args[0]
    assert order["destination"]["address"] in photo.await_args.kwargs["caption"]
    assert "3.00 USDT" in photo.await_args.kwargs["caption"]
    catalog.assert_not_awaited()


def test_changing_plan_cancels_order_before_catalog_is_opened(monkeypatch):
    callback, state = _payment_callback(f"payment:order:change:{ORDER_ID}")
    monkeypatch.setattr(payments, "get_telegram_user", AsyncMock(return_value={"effective_language": "en"}))
    calls = []
    async def cancel(order_id, telegram_id):
        calls.append("cancel")
        assert order_id == ORDER_ID
    async def open_catalog(callback, state):
        calls.append("catalog")
        assert await state.get_state() is None
    monkeypatch.setattr(payments, "cancel_payment_order", cancel)
    monkeypatch.setattr(payments, "open_payment_offers", open_catalog)
    async def exercise():
        await state.set_state(PaymentStates.waiting_for_txid)
        await state.update_data(order_id=ORDER_ID)
        await payments.saved_payment_order_action(callback, state)
    asyncio.run(exercise())
    assert calls == ["cancel", "catalog"]


def test_stale_order_button_cannot_cancel_another_checkout(monkeypatch):
    callback, state = _payment_callback(f"payment:order:cancel:{ORDER_ID}")
    cancel = AsyncMock(); answer = AsyncMock()
    monkeypatch.setattr(payments, "get_telegram_user", AsyncMock(return_value={"effective_language": "en"}))
    monkeypatch.setattr(payments, "cancel_payment_order", cancel)
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    async def exercise():
        await state.update_data(order_id="a-different-order")
        await payments.saved_payment_order_action(callback, state)
        assert (await state.get_data())["order_id"] == "a-different-order"
    asyncio.run(exercise())
    cancel.assert_not_awaited()
    assert answer.await_args.kwargs["show_alert"]


def test_language_switch_cannot_show_usdt_in_persian(monkeypatch):
    callback, state = _payment_callback("payment:open")
    photo = AsyncMock()
    monkeypatch.setattr(Message, "answer_photo", photo)
    async def exercise():
        try:
            await payments._show_payment_order(callback.message, state, saved_order(), "fa")
        except BackendAPIError as exc:
            assert exc.detail["code"] == "payment_order_language"
        else:
            raise AssertionError("USDT instructions must not open in Persian")
    asyncio.run(exercise())
    photo.assert_not_awaited()


def test_usdt_amount_and_order_controls_preserve_precision_language_and_callback_limits():
    assert format_usdt("3.1250") == "3.125 USDT"
    assert format_usdt("0.0001") == "0.0001 USDT"
    assert format_usdt("3") == "3.00 USDT"
    text, keyboard = payments._saved_order_summary(saved_order(), "en")
    assert not re.search(r"[\u0600-\u06ff]", text)
    for row in keyboard.inline_keyboard + build_usdt_screenshot_keyboard(ORDER_ID).inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
            assert ORDER_ID in button.callback_data
            assert not re.search(r"[\u0600-\u06ff]", button.text)
    other_language = deepcopy(saved_order()); other_language["currency"] = "IRT"
    text, keyboard = payments._saved_order_summary(other_language, "en")
    assert not re.search(r"[\u0600-\u06ff]", text)
    assert not any(":resume:" in button.callback_data for row in keyboard.inline_keyboard for button in row)
