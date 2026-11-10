import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, Chat, Message, User

from app import main as main_module
from app.handlers import experience, language as language_handler
from app import runtime_config
from app.services.backend import BackendAPIError


def configuration(language="fa"):
    result = runtime_config.fallback_configuration(language)
    result["required_channels"] = [{"chat_id": "-100123", "title": "MediaHub Channel", "invite_url": "https://t.me/mediahub_test"}]
    return result


@pytest.mark.parametrize("language", ["fa", "en"])
def test_start_shows_required_channels_before_home_menu(monkeypatch, language):
    user = {"is_admin": False, "effective_language": language}
    config = configuration(language)
    monkeypatch.setattr(main_module, "register_telegram_user", AsyncMock(return_value=user))
    monkeypatch.setattr(main_module, "runtime_configuration", AsyncMock(return_value=config))
    monkeypatch.setattr(experience, "runtime_configuration", AsyncMock(return_value=config))
    entitlement = AsyncMock(return_value={"forced_join_required": True})
    monkeypatch.setattr(experience, "get_download_entitlement", entitlement)
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.LEFT)))
    message = SimpleNamespace(from_user=SimpleNamespace(id=42, language_code=language), chat=SimpleNamespace(type="private"), bot=bot, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={}))
    asyncio.run(main_module.start_handler(message, state))
    message.answer.assert_awaited_once()
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/mediahub_test"
    assert markup.inline_keyboard[-1][0].callback_data == "membership:check:home"
    entitlement.assert_awaited_once_with(42)


@pytest.mark.parametrize("admin,required,status", [
    (True, True, ChatMemberStatus.LEFT),
    (False, False, ChatMemberStatus.LEFT),
    (False, True, ChatMemberStatus.MEMBER),
])
def test_start_keeps_admin_and_plan_exemptions_and_accepts_members(monkeypatch, admin, required, status):
    config = configuration()
    monkeypatch.setattr(main_module, "register_telegram_user", AsyncMock(return_value={"is_admin": admin, "effective_language": "fa"}))
    monkeypatch.setattr(main_module, "runtime_configuration", AsyncMock(return_value=config))
    monkeypatch.setattr(experience, "runtime_configuration", AsyncMock(return_value=config))
    entitlement = AsyncMock(return_value={"forced_join_required": required})
    monkeypatch.setattr(experience, "get_download_entitlement", entitlement)
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))
    message = SimpleNamespace(from_user=SimpleNamespace(id=42, language_code="fa"), chat=SimpleNamespace(type="private"), bot=bot, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={}))
    asyncio.run(main_module.start_handler(message, state))
    assert message.answer.await_args.kwargs["reply_markup"].is_persistent
    if admin:
        entitlement.assert_not_awaited()


def test_configuration_failure_cannot_turn_required_membership_into_empty_allowlist(monkeypatch):
    monkeypatch.setattr(runtime_config, "get_bot_configuration", AsyncMock(side_effect=OSError("Backend unavailable")))
    monkeypatch.setattr(experience, "get_download_entitlement", AsyncMock(return_value={"forced_join_required": True}))
    async def run():
        runtime_config.clear_runtime_configuration_cache()
        try:
            cached_fallback = await runtime_config.runtime_configuration("fa")
            assert cached_fallback["required_channels"] == []
            message = SimpleNamespace(answer=AsyncMock(), bot=SimpleNamespace(get_chat_member=AsyncMock()))
            assert not await experience.entry_membership_allowed(message, telegram_id=42,
                user={"is_admin": False}, configuration=cached_fallback)
            message.bot.get_chat_member.assert_not_awaited()
            assert "در دسترس نیست" in message.answer.await_args.args[0]
        finally:
            runtime_config.clear_runtime_configuration_cache()
    asyncio.run(run())


def test_membership_confirmation_opens_home_after_success(monkeypatch):
    config = configuration()
    monkeypatch.setattr(experience, "_user_and_configuration", AsyncMock(return_value=({"is_admin": False}, config)))
    monkeypatch.setattr(experience, "runtime_configuration", AsyncMock(return_value=config))
    monkeypatch.setattr(experience, "get_download_entitlement", AsyncMock(return_value={"forced_join_required": True}))
    answer, edit = AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER)))
    message = Message(message_id=3, date=datetime.now(timezone.utc), chat=Chat(id=42, type="private")).as_(bot)
    callback = CallbackQuery(id="check", chat_instance="test", from_user=User(id=42, is_bot=False, first_name="Test"),
        data="membership:check:home", message=message)
    asyncio.run(experience.check_membership(callback))
    edit.assert_awaited_once()
    assert answer.await_args.kwargs["reply_markup"].is_persistent


def test_language_selection_keeps_nonmember_at_channel_gate(monkeypatch):
    config = configuration("en")
    monkeypatch.setattr(language_handler, "set_user_language", AsyncMock(return_value={"is_admin": False}))
    monkeypatch.setattr(language_handler, "runtime_configuration", AsyncMock(return_value=config))
    gate = AsyncMock(return_value=False)
    monkeypatch.setattr(language_handler, "entry_membership_allowed", gate)
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "edit_text", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    message = Message(message_id=3, date=datetime.now(timezone.utc), chat=Chat(id=42, type="private"))
    callback = CallbackQuery(id="lang", chat_instance="test", from_user=User(id=42, is_bot=False, first_name="Test"),
        data="language:set:en", message=message)
    asyncio.run(language_handler.select_language(callback))
    assert gate.await_args.kwargs["telegram_id"] == 42
    answer.assert_not_awaited()
