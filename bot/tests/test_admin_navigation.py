"""Exercise the real router order with Telegram and backend calls stubbed."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from app import main
from app.handlers import admin, admin_experience, admin_finance, home
from app.runtime_config import fallback_configuration
from app.state.admin_experience import AdminExperienceStates


@pytest.fixture
def ui(monkeypatch):
    message_answer = AsyncMock()
    message_edit = AsyncMock()
    callback_answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", message_answer)
    monkeypatch.setattr(Message, "edit_text", message_edit)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    bot = Bot("123456789:test-token")
    user = User(id=12345, is_bot=False, first_name="Test", language_code="fa")
    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=bot.id, chat_id=user.id, user_id=user.id),
    )

    async def dispatch(*, callback=None, text=None):
        message = Message(
            message_id=10,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user.id, type="private"),
            from_user=user,
            text=text or "پنل مدیریت",
        )
        if callback is not None:
            event = CallbackQuery(
                id="test-callback", from_user=user, chat_instance="test",
                message=message, data=callback,
            )
            update_type = "callback_query"
        else:
            event, update_type = message, "message"
        return await main.dp.propagate_event(
            update_type=update_type, event=event, bot=bot, state=state,
            raw_state=await state.get_state(),
        )

    return SimpleNamespace(
        dispatch=dispatch, state=state, user=user,
        answer=message_answer, edit=message_edit, callback_answer=callback_answer,
    )


@pytest.mark.parametrize("language,item,text", [
    ("fa", "buy", "💎 خرید اشتراک"),
    ("fa", "buy", "خرید اشتراک"),
    ("en", "buy", "💎 Buy subscription"),
    ("fa", "subscription", "👤 وضعیت اشتراک من"),
])
def test_editing_home_button_label_stays_in_editor(ui, monkeypatch, language, item, text):
    context = {"is_admin": True, "permissions": ["settings.manage"]}
    monkeypatch.setattr(admin_experience, "get_admin_context", AsyncMock(return_value=context))
    row = {
        "key": f"bot.buttons.{language}", "category": "bot", "version": 4,
        "value": {"buy": "Old buy", "subscription": "Old subscription"},
    }
    monkeypatch.setattr(admin_experience, "list_application_settings", AsyncMock(return_value=[row]))
    save = AsyncMock()
    monkeypatch.setattr(admin_experience, "update_application_setting", save)
    purchase = AsyncMock()
    register = AsyncMock(return_value={"effective_language": language, "is_admin": True})
    monkeypatch.setattr(home, "send_payment_offers_menu", purchase)
    monkeypatch.setattr(home, "send_subscription_status", purchase)
    monkeypatch.setattr(home, "register_telegram_user", register)
    configurations = (fallback_configuration("fa"), fallback_configuration("en"))
    configurations[0 if language == "fa" else 1]["buttons"][item] = text
    monkeypatch.setattr(home, "all_runtime_configurations", AsyncMock(return_value=configurations))
    monkeypatch.setattr(home, "runtime_configuration", AsyncMock(return_value=configurations[0]))

    async def exercise():
        await ui.dispatch(callback=f"admin:copy:item:{language}:buttons:{item}")
        assert await ui.state.get_state() == AdminExperienceStates.waiting_for_copy_value.state
        assert "مقدار جدید" in ui.edit.await_args.args[0]
        await ui.dispatch(text=text)
        save.assert_awaited_once()
        assert save.await_args.kwargs["key"] == row["key"]
        assert save.await_args.kwargs["value"] == {**row["value"], item: text}
        assert save.await_args.kwargs["expected_version"] == 4
        assert await ui.state.get_state() is None
        purchase.assert_not_awaited()
        register.assert_not_awaited()

    asyncio.run(exercise())


def test_home_purchase_still_opens_when_no_form_is_active(ui, monkeypatch):
    purchase = AsyncMock()
    monkeypatch.setattr(home, "send_payment_offers_menu", purchase)
    monkeypatch.setattr(home, "register_telegram_user", AsyncMock(return_value={"effective_language": "fa"}))
    monkeypatch.setattr(home, "runtime_configuration", AsyncMock(return_value=fallback_configuration("fa")))
    asyncio.run(ui.dispatch(text="💎 خرید اشتراک"))
    purchase.assert_awaited_once()


@pytest.mark.parametrize("permissions", [
    ["roles.manage"], ["admins.manage"], ["admins.manage", "roles.manage"], [],
])
def test_admin_management_preserves_permission_boundaries(ui, monkeypatch, permissions):
    context = {"is_admin": True, "is_superadmin": False, "permissions": permissions}
    monkeypatch.setattr(admin, "_context_or_none", AsyncMock(return_value=context))
    accounts = AsyncMock(return_value=[{"telegram_id": 999, "is_active": True}])
    monkeypatch.setattr(admin, "list_admin_accounts", accounts)

    asyncio.run(ui.dispatch(callback="admin:accounts"))

    if not permissions:
        ui.edit.assert_not_awaited()
        accounts.assert_not_awaited()
        assert ui.callback_answer.await_args.kwargs["show_alert"] is True
        return
    markup = ui.edit.await_args.kwargs["reply_markup"]
    callbacks = {button.callback_data for row in markup.inline_keyboard for button in row}
    assert ("admin:roles" in callbacks) == ("roles.manage" in permissions)
    assert ("admin:account:add" in callbacks) == ("admins.manage" in permissions)
    assert ("admin:account:999" in callbacks) == ("admins.manage" in permissions)
    assert accounts.await_count == int("admins.manage" in permissions)


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "yearly", "all"])
def test_payment_statistics_selects_only_requested_period(ui, monkeypatch, period):
    monkeypatch.setattr(admin_finance, "get_admin_context", AsyncMock(return_value={
        "is_admin": True, "permissions": ["payments.view"],
    }))
    summary = AsyncMock(return_value={"statistics": {
        "daily": {"approved": 91, "irt_total": 79000, "usdt_total": "3.1250"},
        "weekly": {"approved": 92}, "monthly": {"approved": 93},
        "yearly": {"approved": 94}, "all": {"approved": 95},
    }})
    monkeypatch.setattr(admin_finance, "get_admin_payment_summary", summary)

    async def exercise():
        await ui.dispatch(callback="admin:pay:stats")
        summary.assert_not_awaited()
        menu = ui.edit.await_args.kwargs["reply_markup"]
        choices = {button.callback_data for row in menu.inline_keyboard for button in row}
        assert f"admin:pay:stats:{period}" in choices
        await ui.dispatch(callback=f"admin:pay:stats:{period}")
        summary.assert_awaited_once_with(ui.user.id)
        text = ui.edit.await_args.args[0]
        selected_count = summary.return_value["statistics"][period]["approved"]
        assert f"<code>{selected_count}</code>" in text
        for other_count in {91, 92, 93, 94, 95} - {selected_count}:
            assert f"<code>{other_count}</code>" not in text
        menu = ui.edit.await_args.kwargs["reply_markup"]
        choices = {button.callback_data for row in menu.inline_keyboard for button in row}
        assert "admin:pay:stats" in choices
        assert "admin:payments" in choices
        assert "payment:open" not in choices

    asyncio.run(exercise())


@pytest.mark.parametrize("permission,callback", [
    (False, "admin:pay:stats"),
    (False, "admin:pay:stats:daily"),
    (True, "admin:pay:stats:unknown"),
])
def test_statistics_rejects_forbidden_or_invalid_requests(ui, monkeypatch, permission, callback):
    monkeypatch.setattr(admin_finance, "get_admin_context", AsyncMock(return_value={
        "is_admin": True, "permissions": ["payments.view"] if permission else [],
    }))
    summary = AsyncMock()
    monkeypatch.setattr(admin_finance, "get_admin_payment_summary", summary)
    asyncio.run(ui.dispatch(callback=callback))
    summary.assert_not_awaited()
    ui.edit.assert_not_awaited()
    assert ui.callback_answer.await_args.kwargs["show_alert"] is True
