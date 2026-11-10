import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, User

from app.handlers import broadcasts
from app.keyboards.admin import build_admin_home_keyboard
from app.middleware.interface import InterfaceRequests, ui_actor, ui_language, ui_state, ui_passthrough
from app.services import broadcast_sender as sender
from app.services.backend import BackendAPIError


def message(bot=None, **fields):
    return Message(**{"message_id": 10, "date": datetime.now(timezone.utc),
        "chat": Chat(id=42, type="private"), "from_user": User(id=42, is_bot=False, first_name="Admin"),
        **fields}).as_(bot)


def callback(data, bot=None):
    return CallbackQuery(id="cb", from_user=User(id=42, is_bot=False, first_name="Admin"),
        chat_instance="test", data=data, message=message(bot,
        from_user=User(id=999, is_bot=True, first_name="Bot"))).as_(bot)


def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=999, chat_id=42, user_id=42))


def job(**changes):
    return {"id": 7, "actor_telegram_id": 42, "status": "draft", "counts": {"pending": 2},
        "total": 2, "languages": {"fa": 1, "en": 1}, "confirmation_token": str(uuid.uuid4()),
        "expires_at": (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
        "payload": {"segment": "manual", "language": "all", "variants": {
            "fa": {"kind": "text", "text": "سلام"}, "en": {"kind": "text", "text": "Hello"}}, "buttons": {}},
        **changes}


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(broadcasts, "get_admin_context", AsyncMock(return_value={"is_admin": True,
        "is_superadmin": False, "permissions": ["broadcasts.manage"]}))
    monkeypatch.setattr(Message, "answer", AsyncMock(return_value=message()))
    monkeypatch.setattr(Message, "edit_text", AsyncMock(return_value=message()))
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(broadcasts, "deliver", AsyncMock())
    monkeypatch.setattr(broadcasts, "api", AsyncMock(return_value=job()))


@pytest.mark.asyncio
async def test_composer_keeps_two_variants_private_until_final_confirmation(ui):
    fsm = state()
    await broadcasts.new(callback("broadcast:new"), fsm)
    await broadcasts.segment(callback("broadcast:segment:manual"), fsm)
    await broadcasts.manual(message(text="42, 43 42"), fsm)
    await broadcasts.language(callback("broadcast:language:all"), fsm)
    await broadcasts.mode(callback("broadcast:mode:content"), fsm)
    await broadcasts.content(message(text="سلام"), fsm)
    await broadcasts.skip_button(callback("broadcast:button:skip"), fsm)
    broadcasts.api.assert_not_awaited()
    assert (await fsm.get_data())["current_language"] == "en"
    await broadcasts.content(message(text="Hello"), fsm)
    await broadcasts.button(message(text="Details | https://example.com"), fsm)
    broadcasts.api.assert_awaited_once()
    creation = broadcasts.api.await_args
    assert creation.args == ("POST", "/admin/broadcasts", 42)
    assert creation.kwargs["telegram_ids"] == [42, 43]
    assert creation.kwargs["variants"]["fa"]["text"] == "سلام"
    assert creation.kwargs["variants"]["en"]["text"] == "Hello"
    assert creation.kwargs["buttons"] == {"en": {"text": "Details", "url": "https://example.com"}}
    assert broadcasts.deliver.await_count == 2
    assert all(call.args[1] == 42 for call in broadcasts.deliver.await_args_list)
    assert await fsm.get_state() == broadcasts.BroadcastStates.confirming.state
    draft_data = await fsm.get_data()
    broadcasts.api.return_value = job(status="queued")
    await broadcasts.confirm(callback("broadcast:confirm"), fsm)
    assert broadcasts.api.await_args.args == ("POST", "/admin/broadcasts/7/confirm", 42)
    assert broadcasts.api.await_args.kwargs["confirmation_token"] == draft_data["confirmation_token"]
    assert await fsm.get_data() == {}


@pytest.mark.asyncio
async def test_skip_button_uses_callback_actor_not_bot_message_sender(ui):
    fsm = state()
    await fsm.update_data(request_id=str(uuid.uuid4()), segment="all", plan_id=None, telegram_ids=[],
        language="fa", current_language="fa", variants={"fa": {"kind": "text", "text": "سلام"}}, buttons={})
    await broadcasts.skip_button(callback("broadcast:button:skip"), fsm)
    assert broadcasts.api.await_args.args[2] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["source", "expired", "owner", "empty"])
async def test_failed_or_invalid_preview_has_no_send_confirmation(ui, failure):
    fsm = state()
    draft = job()
    if failure == "source":
        broadcasts.deliver.side_effect = RuntimeError("Source unavailable")
    elif failure == "expired":
        draft["expires_at"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    elif failure == "owner":
        draft["actor_telegram_id"] = 99
    else:
        draft["total"], draft["languages"] = 0, {}
    await broadcasts.preview_job(message(), fsm, 42, draft)
    broadcasts.api.assert_not_awaited()
    actions = [b.callback_data for call in Message.answer.await_args_list
               for row in (call.kwargs.get("reply_markup").inline_keyboard if call.kwargs.get("reply_markup") else [])
               for b in row]
    assert "broadcast:confirm" not in actions


@pytest.mark.asyncio
async def test_private_and_permission_checks_block_form_and_final_send(ui):
    broadcasts.get_admin_context.return_value = {"is_admin": True, "permissions": ["payments.review"]}
    await broadcasts.new(callback("broadcast:new"), state())
    await broadcasts.confirm(callback("broadcast:confirm"), state())
    await broadcasts.content(message(text="Cannot send"), state())
    assert not await broadcasts.allowed(message(chat=Chat(id=-100, type="supergroup")))
    broadcasts.api.assert_not_awaited()
    broadcasts.deliver.assert_not_awaited()


@pytest.mark.parametrize("kind,extra", [
    ("photo", {"photo": [{"file_id": "small", "file_unique_id": "s", "width": 10, "height": 10},
                         {"file_id": "full", "file_unique_id": "f", "width": 100, "height": 100}]}),
    ("video", {"video": {"file_id": "full", "file_unique_id": "f", "width": 100, "height": 100, "duration": 5}}),
    ("document", {"document": {"file_id": "full", "file_unique_id": "f"}}),
])
def test_media_preserves_file_and_caption_entities(kind, extra):
    result = broadcasts.content_from_message(message(caption="Caption", caption_entities=[
        {"type": "bold", "offset": 0, "length": 7}], **extra), "content")
    assert result == {"kind": kind, "file_id": "full", "text": "Caption",
                      "entities": [{"type": "bold", "offset": 0, "length": 7}]}


def test_forward_preserves_origin_and_rejects_albums_protected_and_nonforward():
    original = message(forward_origin={"type": "channel", "date": datetime.now(timezone.utc),
        "chat": {"id": -1001, "type": "channel"}, "message_id": 5})
    assert broadcasts.content_from_message(original, "forward") == {
        "kind": "forward", "source_chat_id": 42, "source_message_id": 10}
    for invalid, mode in ((message(text="x"), "forward"), (message(text="x", media_group_id="g"), "content"),
                          (message(text="x", has_protected_content=True), "content")):
        with pytest.raises(ValueError):
            broadcasts.content_from_message(invalid, mode)


@pytest.mark.asyncio
async def test_navigation_is_not_consumed_as_broadcast_text(monkeypatch):
    monkeypatch.setattr(broadcasts, "all_runtime_configurations", AsyncMock(return_value=()))
    for value in ("/start", "/admin", "👤 My subscription", "💎 خرید اشتراک"):
        assert not await broadcasts.BroadcastInput()(SimpleNamespace(text=value))
    assert await broadcasts.BroadcastInput()(SimpleNamespace(text="A normal broadcast"))
    assert await broadcasts.BroadcastInput()(SimpleNamespace(text=None))


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["broadcast:confirm", "broadcast:action:resume:7", "broadcast:action:cancel:7"])
async def test_final_actions_use_existing_actor_message_and_form_bound_gate(action):
    fsm = state()
    await fsm.update_data(broadcast_id=7, confirmation_token=str(uuid.uuid4()))
    tokens = ui_actor.set(42), ui_state.set(fsm)
    redis = SimpleNamespace(set=AsyncMock())
    try:
        method = SendMessage(chat_id=42, text="Preview", reply_markup=broadcasts.keyboard([[('Confirm', action)]]))
        send = AsyncMock(return_value=message())
        await InterfaceRequests(redis)(send, None, method)
        assert send.call_args.args[1].reply_markup.inline_keyboard[0][0].callback_data.startswith("gate:")
        assert redis.set.await_count == 2  # Pre-send token, then bind the actual Telegram message.
        stored = json.loads(redis.set.await_args.args[1])
        assert stored["actor"] == stored["chat"] == 42
        assert stored["message"] == 10 and stored["action"] == action and stored["state"]
    finally:
        ui_actor.reset(tokens[0])
        ui_state.reset(tokens[1])


def test_menu_permission_and_english_report():
    def actions(permissions):
        return {b.callback_data for row in build_admin_home_keyboard(permissions, is_superadmin=False).inline_keyboard for b in row}
    assert "broadcast:home:1" in actions({"broadcasts.manage"})
    assert "broadcast:home:1" not in actions({"payments.review"})
    token = ui_language.set("en")
    try:
        text = broadcasts.report(job(status="paused", counts={"uncertain": 1, "pending": 1}, last_error="transport_uncertain"))
        assert "Uncertain" in text and "not replayed" in text
        assert not any('\u0600' <= ch <= '\u06ff' for ch in text)
    finally:
        ui_language.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "photo", "video", "document", "forward"])
async def test_delivery_uses_exact_content_without_html_and_bounded_timeout(kind):
    bot = SimpleNamespace(**{f"{prefix}_{method}": AsyncMock(return_value=SimpleNamespace(message_id=3))
        for prefix, method in [("send", "message"), ("send", "photo"), ("send", "video"),
                               ("send", "document"), ("forward", "message")]})
    content = {"kind": kind, "text": "<literal>", "entities": [], "file_id": "file",
               "source_chat_id": 42, "source_message_id": 10}
    await sender.deliver(bot, 43, content, None if kind == "forward" else {"text": "Link", "url": "https://example.com"})
    method = bot.forward_message if kind == "forward" else getattr(bot, "send_message" if kind == "text" else f"send_{kind}")
    assert method.await_args.kwargs["chat_id"] == 43
    assert method.await_args.kwargs["request_timeout"] == 45
    if kind != "forward":
        assert method.await_args.kwargs["parse_mode"] is None
        assert method.await_args.kwargs["reply_markup"].inline_keyboard[0][0].url == "https://example.com"
    with pytest.raises(ValueError):
        await sender.deliver(bot, -100, content)
    assert not ui_passthrough.get()


@pytest.mark.asyncio
async def test_long_preview_is_identical_to_delivery_and_not_split_into_admin_pages():
    redis = SimpleNamespace(set=AsyncMock())
    wire = AsyncMock(return_value=message())
    async def send_message(**kwargs):
        kwargs.pop("request_timeout")
        await InterfaceRequests(redis)(wire, None, SendMessage(**kwargs))
    token = ui_actor.set(42)
    try:
        await sender.deliver(SimpleNamespace(send_message=send_message), 42,
                             {"kind": "text", "text": "x" * 4096, "entities": []})
        assert wire.await_args.args[1].text == "x" * 4096
        assert wire.await_args.args[1].reply_markup is None
        redis.set.assert_not_awaited()
    finally:
        ui_actor.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [
    ("flood", {"outcome": "retry", "retry_after": 12, "error_code": "flood_wait"}),
    ("blocked", {"outcome": "blocked", "error_code": "forbidden"}),
    ("missing", {"outcome": "failed", "error_code": "chat_unavailable"}),
    ("invalid", {"outcome": "failed", "error_code": "invalid_content"}),
    ("network", {"outcome": "uncertain", "error_code": "transport_uncertain"}),
])
async def test_telegram_outcomes_never_retry_an_ambiguous_delivery(monkeypatch, failure, expected):
    method = SendMessage(chat_id=42, text="Hello")
    errors = {"flood": TelegramRetryAfter(method=method, message="Too many requests", retry_after=12),
        "blocked": TelegramForbiddenError(method=method, message="Forbidden"),
        "missing": TelegramBadRequest(method=method, message="chat not found"),
        "invalid": TelegramBadRequest(method=method, message="invalid file_id"),
        "network": TelegramNetworkError(method=method, message="Disconnected")}
    monkeypatch.setattr(sender, "deliver", AsyncMock(side_effect=errors[failure]))
    assert await sender.send_claim(None, {"broadcast_id": 7, "telegram_id": 43, "content": {}}) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_code", [503, 409, 404])
async def test_failed_ack_retries_ack_only_or_drops_obsolete_claim(monkeypatch, failure_code):
    delivery = {"recipient_id": 17, "broadcast_id": 7, "claim_token": str(uuid.uuid4())}
    calls, ack_calls = [], []
    async def backend(method, path, **kwargs):
        calls.append(path)
        if path.endswith("/claim"):
            if len(calls) > 1:
                raise asyncio.CancelledError()
            return delivery
        ack_calls.append(kwargs["payload"])
        if len(ack_calls) == 1:
            raise BackendAPIError(status_code=failure_code, detail="Lost acknowledgement")
        return {"status": "sent"}
    monkeypatch.setattr(sender, "_payment_request", backend)
    monkeypatch.setattr(sender, "send_claim", AsyncMock(return_value={"outcome": "sent", "message_id": 123}))
    monkeypatch.setattr(sender.asyncio, "sleep", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await sender.run_broadcast_sender(None)
    sender.send_claim.assert_awaited_once()
    assert len(ack_calls) == (2 if failure_code == 503 else 1)
    assert all(payload == {"outcome": "sent", "message_id": 123, "claim_token": delivery["claim_token"]}
               for payload in ack_calls)
