import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.middleware.interface import ui_language
from app.runtime_config import clear_runtime_configuration_cache
from app.services import topic_delivery as delivery


TICKET = {
    "id": 123, "category": "download", "status": "new", "plan_name": "Pro",
    "user": {"telegram_id": 900, "first_name": "Example", "username": "example"},
    "messages": [{"body": "Earlier message"}, {"body": "Latest <b>literal</b> & text"}],
    "recipients": [101, 102, 101], "recipient_languages": {"101": "fa", "102": "en"},
}


@pytest.fixture
def setup(monkeypatch):
    clear_runtime_configuration_cache()
    monkeypatch.setattr(delivery, "runtime_configuration", AsyncMock(return_value={}))
    monkeypatch.setattr(delivery, "notification_routes", AsyncMock(return_value={"enabled": False}))
    patch = AsyncMock()
    monkeypatch.setattr(delivery, "_payment_request", patch)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=17)),
                          send_photo=AsyncMock(), send_voice=AsyncMock())
    yield bot, patch
    clear_runtime_configuration_cache()


@pytest.mark.asyncio
async def test_private_tickets_do_not_require_a_topic_and_use_each_admins_language(setup):
    bot, patch = setup
    token = ui_language.set("en")
    try:
        assert await delivery.send_support_notification(bot, TICKET, initial=True)
        assert ui_language.get() == "en"
    finally:
        ui_language.reset(token)
    calls = [call.kwargs for call in bot.send_message.await_args_list]
    assert [call["chat_id"] for call in calls] == [101, 102]
    assert "تیکت پشتیبانی جدید" in calls[0]["text"]
    assert "New support ticket #123" in calls[1]["text"]
    for call in calls:
        assert "Latest <b>literal</b> & text" in call["text"]
        assert "Earlier" not in call["text"]
        assert call["parse_mode"] is None and "message_thread_id" not in call
        actions = {button.callback_data for row in call["reply_markup"].inline_keyboard for button in row}
        assert actions == {"support_admin:reply:123", "support_admin:close:123", "admin:support:ticket:123"}
    patch.assert_not_called()


@pytest.mark.asyncio
async def test_blocked_administrator_and_unavailable_topic_do_not_block_other_alerts(setup, monkeypatch):
    bot, _ = setup
    async def send(**kwargs):
        if kwargs["chat_id"] == 101:
            raise RuntimeError("blocked")
        return SimpleNamespace(message_id=18)
    bot.send_message.side_effect = send
    monkeypatch.setattr(delivery, "notification_routes", AsyncMock(side_effect=RuntimeError("offline")))
    assert await delivery.send_support_notification(bot, TICKET)
    assert bot.send_message.await_count == 2
    assert "New user reply" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_attachment_arrives_as_reply_to_each_private_notification(setup):
    bot, _ = setup
    ticket = copy.deepcopy(TICKET)
    ticket["messages"].append({"body": None, "file_type": "voice", "telegram_file_id": "voice-fixture"})
    assert await delivery.send_support_notification(bot, ticket)
    assert bot.send_voice.await_count == 2
    for call in bot.send_voice.await_args_list:
        assert call.kwargs["voice"] == "voice-fixture"
        assert call.kwargs["reply_parameters"].message_id == 17
        assert "message_thread_id" not in call.kwargs


@pytest.mark.asyncio
async def test_attachment_or_topic_bookkeeping_failure_keeps_delivered_status(setup, monkeypatch):
    bot, patch = setup
    ticket = copy.deepcopy(TICKET)
    ticket["messages"] = [{"file_type": "photo", "telegram_file_id": "photo-fixture"}]
    bot.send_photo.side_effect = RuntimeError("bad file")
    monkeypatch.setattr(delivery, "notification_routes", AsyncMock(return_value={
        "enabled": True, "chat_id": -100123, "topics": {"support": 42}}))
    patch.side_effect = RuntimeError("backend unavailable")
    assert await delivery.send_support_notification(bot, ticket, initial=True)
    assert bot.send_message.await_count == 3 and bot.send_photo.await_count == 3
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 42
    assert patch.await_args.kwargs["payload"]["admin_chat_id"] == -100123


@pytest.mark.asyncio
async def test_empty_private_recipients_still_use_configured_topic(setup, monkeypatch):
    bot, _ = setup
    ticket = {**TICKET, "recipients": [False, -100123, "101", 0]}
    assert not await delivery.send_support_notification(bot, ticket)
    bot.send_message.assert_not_called()
    monkeypatch.setattr(delivery, "notification_routes", AsyncMock(return_value={
        "enabled": True, "chat_id": -100123, "topics": {"support": 42}}))
    assert await delivery.send_support_notification(bot, ticket)
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_copy_and_long_emoji_are_safe_plain_text(setup, monkeypatch):
    from app import runtime_config
    bot, _ = setup
    runtime_config._cache["fa"] = (float("inf"), {"tool_texts": {
        "support_private_reply": "درخواست #{ticket_id}: {body} {body} {body}"}})
    ticket = copy.deepcopy(TICKET)
    ticket["recipients"] = [101]
    ticket["messages"] = [{"body": "🎵<&" * 4000}]
    assert await delivery.send_support_notification(bot, ticket)
    text = bot.send_message.await_args.kwargs["text"]
    assert text.startswith("درخواست #123:")
    assert len(text.encode("utf-16-le")) <= 3900 * 2
    assert text.endswith("…")
