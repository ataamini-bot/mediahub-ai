import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, User

from app.handlers import payments
from app.handlers.payments import (
    _offer_details_text,
    _payment_destination_text,
    _payment_error_message,
    continue_payment_offer,
    cancel_payment_flow,
    open_payment_offers,
    select_usdt_destination,
)
from app.keyboards.experience import build_support_categories_keyboard
from app.keyboards.payment import build_payment_offer_detail_keyboard
from app.runtime_config import fallback_configuration, runtime_content
from app.services.backend import BackendAPIError
from app.state.payment import PaymentStates
from app.utils.payment_qr import build_usdt_address_qr


def test_english_support_and_payment_controls_have_no_persian_text():
    support = build_support_categories_keyboard("en")
    assert all("انصراف" not in button.text for row in support.inline_keyboard for button in row)
    detail = build_payment_offer_detail_keyboard("en")
    assert all("پرداخت" not in button.text for row in detail.inline_keyboard for button in row)


def test_english_payment_errors_and_offer_details_are_english():
    error = _payment_error_message(BackendAPIError(status_code=503, detail="missing"), "en")
    assert "سیستم" not in error and "USDT" in error
    text = _offer_details_text({
        "label": "Pro", "duration_days": 30, "price": "2.5", "currency": "USDT",
        "description": "Fast downloads", "daily_download_limit": None,
        "max_file_size_mb": 1000, "max_quality": 1080, "max_concurrent_downloads": 2,
        "priority_processing": True, "forced_join_required": False,
    }, "en")
    assert "Duration" in text and "توضیح" not in text


def test_persian_runtime_copy_cannot_leak_into_english():
    configuration = fallback_configuration("en")
    configuration["content"]["support_sent"] = "شناسه پیگیری"
    assert "شناسه" not in runtime_content(configuration, "support_sent")


def test_usdt_address_qr_is_a_png_and_contains_no_private_material():
    qr_png = build_usdt_address_qr("TVjsPublicLBankDepositAddress123")

    assert qr_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(qr_png) > 500


def test_usdt_destination_instructs_user_to_scan_qr_in_english():
    text = _payment_destination_text(
        {
            "label": "Global",
            "duration_days": 30,
            "price": "3",
            "currency": "USDT",
        },
        {
            "network_name": "TRON (TRC20)",
            "asset_symbol": "USDT",
            "address": "TVjsPublicLBankDepositAddress123",
        },
        {"max_size_mb": 10},
        "en",
    )

    assert "Scan the QR code" in text
    assert "شبکه" not in text


def _payment_callback(
    data: str,
    *,
    photo: bool = False,
) -> tuple[CallbackQuery, FSMContext]:
    user = User(
        id=12345,
        is_bot=False,
        first_name="Test",
        language_code="en",
    )
    message_data = dict(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user.id, type="private"),
        from_user=user,
    )
    if photo:
        message_data.update(
            caption="USDT payment address",
            photo=[
                PhotoSize(
                    file_id="photo-id",
                    file_unique_id="photo-unique-id",
                    width=512,
                    height=512,
                )
            ],
        )
    else:
        message_data["text"] = "Payment"
    message = Message(**message_data)
    callback = CallbackQuery(
        id="test-callback",
        from_user=user,
        chat_instance="test",
        message=message,
        data=data,
    )
    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=999, chat_id=user.id, user_id=user.id),
    )
    return callback, state


def _usdt_configuration() -> dict:
    return {
        "offers": [
            {
                "code": "global",
                "label": "Global",
                "duration_days": 30,
                "price": "3",
                "currency": "USDT",
            }
        ],
        "destination": None,
        "destinations": [
            {
                "id": 11,
                "network_name": "TRON",
                "network_code": "TRC20",
                "asset_symbol": "USDT",
                "address": "TFirstPublicAddress1111111111111111111",
            },
            {
                "id": 12,
                "network_name": "Ethereum",
                "network_code": "ERC20",
                "asset_symbol": "USDT",
                "address": "0x2222222222222222222222222222222222222222",
            },
        ],
        "receipt": {"max_size_mb": 10, "allowed_types": ["photo"]},
    }


def test_continuing_english_offer_requires_explicit_network_selection(
    monkeypatch,
):
    callback, state = _payment_callback("payment:offer:continue")
    edit_text = AsyncMock()
    callback_answer = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit_text)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    monkeypatch.setattr(
        payments,
        "get_telegram_user",
        AsyncMock(return_value={"effective_language": "en"}),
    )
    monkeypatch.setattr(
        payments,
        "get_payment_configuration",
        AsyncMock(return_value=_usdt_configuration()),
    )

    async def exercise():
        await state.set_state(PaymentStates.confirming_offer)
        await state.update_data(
            offer={"code": "global", "currency": "USDT"},
            offer_code="global",
        )
        await continue_payment_offer(callback, state)

        assert (
            await state.get_state()
            == PaymentStates.selecting_usdt_destination.state
        )
        assert (await state.get_data())["usdt_destination_id"] is None

    asyncio.run(exercise())
    assert "Choose the USDT transfer network" in edit_text.await_args.args[0]
    callbacks = [
        row[0].callback_data
        for row in edit_text.await_args.kwargs["reply_markup"].inline_keyboard[:2]
    ]
    assert callbacks == [
        "payment:usdt-destination:11",
        "payment:usdt-destination:12",
    ]


def test_selected_usdt_network_is_revalidated_before_showing_its_qr(
    monkeypatch,
):
    callback, state = _payment_callback("payment:usdt-destination:12")
    answer_photo = AsyncMock()
    delete = AsyncMock()
    callback_answer = AsyncMock()
    monkeypatch.setattr(Message, "answer_photo", answer_photo)
    monkeypatch.setattr(Message, "delete", delete)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    monkeypatch.setattr(
        payments,
        "get_telegram_user",
        AsyncMock(return_value={"effective_language": "en"}),
    )
    get_configuration = AsyncMock(return_value=_usdt_configuration())
    monkeypatch.setattr(
        payments,
        "get_payment_configuration",
        get_configuration,
    )
    monkeypatch.setattr(
        payments,
        "build_usdt_address_qr",
        lambda address: b"png:" + address.encode(),
    )

    async def exercise():
        await state.set_state(PaymentStates.selecting_usdt_destination)
        await state.update_data(offer_code="global")
        await select_usdt_destination(callback, state)

        assert await state.get_state() == PaymentStates.waiting_for_receipt.state
        assert (await state.get_data())["usdt_destination_id"] == 12

    asyncio.run(exercise())
    get_configuration.assert_awaited_once_with(
        select_destination=True,
        language="en",
    )
    assert "Ethereum" in answer_photo.await_args.kwargs["caption"]
    assert "0x2222222222222222222222222222222222222222" in (
        answer_photo.await_args.kwargs["caption"]
    )
    assert "TRON" not in answer_photo.await_args.kwargs["caption"]


def test_choose_another_plan_replaces_usdt_qr_photo(monkeypatch):
    callback, state = _payment_callback("payment:open", photo=True)
    answer = AsyncMock()
    delete = AsyncMock()
    edit_text = AsyncMock()
    callback_answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "delete", delete)
    monkeypatch.setattr(Message, "edit_text", edit_text)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    monkeypatch.setattr(
        payments,
        "get_telegram_user",
        AsyncMock(return_value={"effective_language": "en"}),
    )
    monkeypatch.setattr(
        payments,
        "get_payment_configuration",
        AsyncMock(return_value=_usdt_configuration()),
    )

    async def exercise():
        await state.set_state(PaymentStates.waiting_for_receipt)
        await state.update_data(usdt_destination_id=12)
        await open_payment_offers(callback, state)
        assert await state.get_state() is None

    asyncio.run(exercise())
    answer.assert_awaited_once()
    assert "Choose a plan" in answer.await_args.args[0]
    delete.assert_awaited_once_with()
    edit_text.assert_not_awaited()
    callback_answer.assert_awaited_once_with()


def test_cancel_replaces_usdt_qr_photo_and_clears_state(monkeypatch):
    callback, state = _payment_callback("payment:cancel", photo=True)
    answer = AsyncMock()
    delete = AsyncMock()
    edit_text = AsyncMock()
    callback_answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "delete", delete)
    monkeypatch.setattr(Message, "edit_text", edit_text)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    monkeypatch.setattr(
        payments,
        "get_telegram_user",
        AsyncMock(return_value={"effective_language": "en"}),
    )
    monkeypatch.setattr(
        payments,
        "_user_home_inline_keyboard",
        AsyncMock(return_value=None),
    )

    async def exercise():
        await state.set_state(PaymentStates.waiting_for_receipt)
        await state.update_data(usdt_destination_id=12)
        await cancel_payment_flow(callback, state)
        assert await state.get_state() is None

    asyncio.run(exercise())
    answer.assert_awaited_once()
    assert answer.await_args.args[0] == "Subscription purchase cancelled."
    delete.assert_awaited_once_with()
    edit_text.assert_not_awaited()
    callback_answer.assert_awaited_once_with("Cancelled.")
