import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from app.handlers import admin_experience as admin, experience
from app.keyboards.admin_experience import build_channel_language_keyboard, build_channels_admin_keyboard
from app.middleware.interface import ui_language
from app.runtime_config import fallback_configuration, required_channels_for_language
from app.state.admin_experience import AdminExperienceStates


def channels():
    return [
        {"id": i, "chat_id": str(-100000 - i), "title": f"Channel {i}",
         "invite_url": f"https://t.me/channel{i}", "language": language, "is_active": i != 5}
        for i, language in enumerate(("fa", "fa", "en", "all", "en", "fa"), 1)
    ]


def configuration(language):
    result = fallback_configuration(language)
    result["required_channels"] = channels()
    return result


def callback(data, bot=None):
    message = Message(message_id=4, date=datetime.now(timezone.utc), chat=Chat(id=42, type="private"))
    if bot:
        message = message.as_(bot)
    return CallbackQuery(id="test", chat_instance="test", from_user=User(id=42, first_name="Test", is_bot=False),
                         message=message, data=data)


def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))


@pytest.mark.parametrize("language,expected", [("fa", [1, 2, 4, 6]), ("en", [3, 4])])
def test_gate_keeps_joined_channel_visible_and_checks_only_selected_language(monkeypatch, language, expected):
    config = configuration(language)
    monkeypatch.setattr(experience, "runtime_configuration", AsyncMock(return_value=config))
    bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=[
        SimpleNamespace(status=ChatMemberStatus.MEMBER),
        *[SimpleNamespace(status=ChatMemberStatus.LEFT) for _ in expected[1:]],
    ]))
    message = SimpleNamespace(bot=bot, answer=AsyncMock())
    assert not asyncio.run(experience.enforce_required_membership(
        message, telegram_id=42, configuration=config, return_home=True,
    ))
    markup = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].url for row in markup[:-1]] == [f"https://t.me/channel{i}" for i in expected]
    assert markup[0][0].text.startswith("✅ ")
    assert all(row[0].text.startswith("➕ ") for row in markup[1:-1])
    assert [c.kwargs["chat_id"] for c in bot.get_chat_member.await_args_list] == [str(-100000 - i) for i in expected]
    assert markup[-1][0].callback_data == "membership:check:home"


def test_gate_shows_both_unjoined_channels(monkeypatch):
    config = configuration("fa")
    config["required_channels"] = config["required_channels"][:2]
    monkeypatch.setattr(experience, "runtime_configuration", AsyncMock(return_value=config))
    message = SimpleNamespace(answer=AsyncMock(), bot=SimpleNamespace(get_chat_member=AsyncMock(
        return_value=SimpleNamespace(status=ChatMemberStatus.LEFT))))
    assert not asyncio.run(experience.enforce_required_membership(message, telegram_id=42, configuration=config))
    rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].url for row in rows[:-1]] == ["https://t.me/channel1", "https://t.me/channel2"]


def test_check_refreshes_old_buttons_when_channel_added_or_language_changed(monkeypatch):
    old, fresh = configuration("en"), configuration("en")
    old["required_channels"] = []
    monkeypatch.setattr(experience, "_user_and_configuration", AsyncMock(return_value=({"is_admin": False}, old)))
    monkeypatch.setattr(experience, "runtime_configuration", AsyncMock(return_value=fresh))
    monkeypatch.setattr(experience, "get_download_entitlement", AsyncMock(return_value={"forced_join_required": True}))
    edit, answer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    cb = callback("membership:check:home", SimpleNamespace(get_chat_member=AsyncMock(
        side_effect=[SimpleNamespace(status=ChatMemberStatus.MEMBER), SimpleNamespace(status=ChatMemberStatus.LEFT)])))
    asyncio.run(experience.check_membership(cb))
    rows = edit.await_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].url for row in rows[:-1]] == ["https://t.me/channel3", "https://t.me/channel4"]
    assert rows[0][0].text.startswith("✅ ")
    assert rows[1][0].text.startswith("➕ ")
    answer.assert_not_awaited()  # One joined channel must not unlock home.


def test_membership_requires_every_channel_in_the_selected_language(monkeypatch):
    config = configuration("en")
    monkeypatch.setattr(experience, "runtime_configuration", AsyncMock(return_value=config))
    message = SimpleNamespace(answer=AsyncMock(), bot=SimpleNamespace(get_chat_member=AsyncMock(
        return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER))))
    assert asyncio.run(experience.enforce_required_membership(message, telegram_id=42, configuration=config))
    message.answer.assert_not_awaited()
    assert message.bot.get_chat_member.await_count == 2


@pytest.mark.parametrize("language", ["fa", "en"])
def test_existing_channels_without_language_still_apply(language):
    config = configuration(language)
    legacy = {"chat_id": "-100999", "title": "Legacy", "invite_url": "https://t.me/legacy"}
    config["required_channels"] = [legacy]
    assert required_channels_for_language(config) == [legacy]


@pytest.mark.parametrize("language", ["fa", "en", "all"])
def test_create_channel_waits_for_language_and_sends_it_to_backend(monkeypatch, language):
    created = {**channels()[0], "language": language}
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(admin, "create_required_channel", create)
    monkeypatch.setattr(Message, "answer", AsyncMock())
    monkeypatch.setattr(Message, "edit_text", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())

    async def run():
        form = state()
        await form.update_data(channel_chat_id="-100001", channel_title="First")
        message = Message(message_id=3, date=datetime.now(timezone.utc), chat=Chat(id=42, type="private"),
                          from_user=User(id=42, first_name="Admin", is_bot=False), text="https://t.me/channel1")
        await admin.channel_invite_url(message, form)
        create.assert_not_awaited()
        assert await form.get_state() == AdminExperienceStates.selecting_channel_language.state
        await admin.create_channel_with_language(callback(f"admin:channel:set-language:create:{language}"), form)
        assert create.await_args.kwargs["data"]["language"] == language
        assert create.await_args.kwargs["data"]["chat_id"] == "-100001"
        assert create.await_args.kwargs["data"]["invite_url"] == "https://t.me/channel1"
        assert await form.get_state() is None
    asyncio.run(run())


def test_edit_language_changes_only_the_selected_channel(monkeypatch):
    update = AsyncMock(return_value={**channels()[1], "language": "en"})
    monkeypatch.setattr(admin, "update_required_channel", update)
    monkeypatch.setattr(Message, "edit_text", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    asyncio.run(admin.change_channel_language(callback("admin:channel:set-language:2:en"), state()))
    update.assert_awaited_once_with(actor_telegram_id=42, channel_id=2, changes={"language": "en"})


def test_channel_language_options_are_translated_and_all_records_stay_in_admin_list():
    token = ui_language.set("en")
    try:
        labels = [row[0].text for row in build_channel_language_keyboard().inline_keyboard]
        assert labels == ["Persian", "English", "Both languages", "Cancel"]
        rows = build_channels_admin_keyboard(channels()).inline_keyboard
        assert len([row for row in rows if row[0].callback_data.rsplit(":", 1)[-1].isdigit()]) == len(channels())
        assert "User language: English" in admin._channel_text({**channels()[0], "language": "en"})
    finally:
        ui_language.reset(token)
