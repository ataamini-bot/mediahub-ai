import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers import customers
from app.handlers.experience import missing_required_channels
from app.keyboards.admin import build_admin_home_keyboard
from app.middleware.interface import InterfaceRequests, ui_actor, ui_state


class MemoryRedis:
    def __init__(self):
        self.data = {}
    async def set(self, key, value, **kwargs):
        self.data[key] = value


@pytest.mark.parametrize("action", ["customer:confirm", "backup:confirm"])
def test_new_sensitive_actions_receive_bound_confirmation_tokens(action):
    async def run():
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
        await state.update_data(target=17, days=3)
        tokens = ui_actor.set(42), ui_state.set(state)
        try:
            redis = MemoryRedis()
            sent = AsyncMock(return_value=Message(message_id=10, date=datetime.now(timezone.utc), chat=Chat(id=42, type="private")))
            method = SendMessage(chat_id=42, text="Review", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Confirm", callback_data=action)]]))
            await InterfaceRequests(redis)(sent, None, method)
            callback = sent.call_args.args[1].reply_markup.inline_keyboard[0][0].callback_data
            assert callback.startswith("gate:")
            assert len(redis.data) == 1
        finally:
            ui_actor.reset(tokens[0])
            ui_state.reset(tokens[1])
    asyncio.run(run())


def test_support_forms_leave_commands_and_home_buttons_to_navigation(monkeypatch):
    monkeypatch.setattr(customers, "all_runtime_configurations", AsyncMock(return_value=()))
    async def run():
        for value in ("/menu", "/admin", "  /start", "💎 خرید اشتراک", "👤 My subscription"):
            assert not await customers.SupportFormInput()(SimpleNamespace(text=value))
        assert await customers.SupportFormInput()(SimpleNamespace(text="123456789"))
        assert await customers.SupportFormInput()(SimpleNamespace(text="Fixing the failed renewal"))
    asyncio.run(run())


def test_customer_and_backup_menu_respect_distinct_permissions():
    def actions(permissions):
        return {b.callback_data for row in build_admin_home_keyboard(permissions, is_superadmin=False).inline_keyboard for b in row}
    assert "customer:open" not in actions({"payments.review"})
    assert "customer:open" in actions({"users.view"})
    assert "backup:open" not in actions({"users.view"})
    assert "backup:open" in actions({"backups.view"})


def test_membership_handles_telegram_enum_statuses_restricted_and_api_failure():
    async def run():
        bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=[
            SimpleNamespace(status=ChatMemberStatus.MEMBER),
            SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR),
            SimpleNamespace(status=ChatMemberStatus.RESTRICTED, is_member=True),
            SimpleNamespace(status=ChatMemberStatus.LEFT),
            RuntimeError("Telegram unavailable"),
        ]))
        channels = [{"chat_id": -1000 - n, "invite_url": f"https://t.me/channel{n}"} for n in range(5)]
        missing = await missing_required_channels(bot, 42, {"required_channels": channels})
        assert missing == channels[3:]
        assert all(call.kwargs["user_id"] == 42 for call in bot.get_chat_member.await_args_list)
    asyncio.run(run())


def test_support_confirmation_uses_selected_customer_and_idempotency_key(monkeypatch):
    async def run():
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
        await state.update_data(target=99, action="extend", days=7, subscription_id=12,
            request_id="a142625b-529c-4540-8d8b-48f3015172c0", expected_revision="a" * 64,
            reason="Seven days compensation")
        mocked = AsyncMock(return_value={"replayed": False, "customer": {"language": "en"}})
        monkeypatch.setattr(customers, "api", mocked)
        monkeypatch.setattr(customers, "show_profile", AsyncMock())
        cb = SimpleNamespace(from_user=SimpleNamespace(id=42), bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(answer=AsyncMock()), answer=AsyncMock())
        await customers.confirm(cb, state)
        assert mocked.await_args.args == ("POST", "/admin/customers/99/actions", 42)
        assert mocked.await_args.kwargs["subscription_id"] == 12
        assert mocked.await_args.kwargs["request_id"] == "a142625b-529c-4540-8d8b-48f3015172c0"
        assert await state.get_data() == {}
        cb.bot.send_message.assert_awaited_once()
    asyncio.run(run())
