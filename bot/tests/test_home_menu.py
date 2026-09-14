import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import main as main_module
from app.handlers import home as home_handler
from app.handlers.payments import (
    _notify_user_approved,
    _notify_user_rejected,
    _replace_payment_message,
)
from app.i18n import home_action_for_text
from app.keyboards.payment import build_home_reply_keyboard


def _button_texts(keyboard) -> list[str]:
    return [button.text for row in keyboard.keyboard for button in row]


def test_persistent_home_keyboard_is_localized_and_kept_open():
    persian = build_home_reply_keyboard("fa", include_admin=False)
    english_admin = build_home_reply_keyboard("en", include_admin=True)

    assert persian.is_persistent is True
    assert persian.resize_keyboard is True
    assert persian.one_time_keyboard is False
    assert _button_texts(persian) == [
        "💎 خرید اشتراک",
        "👤 وضعیت اشتراک من",
        "🛟 پشتیبانی",
        "🌐 تغییر زبان | Language",
        "🔄 تبدیل فایل",
        "📘 آموزش استفاده",
        "❓ سوالات متداول",
    ]
    assert _button_texts(english_admin)[-1] == "⚙️ Admin panel"


def test_persistent_home_keyboard_keeps_all_custom_buttons_scrollable():
    keyboard = build_home_reply_keyboard(
        "en",
        configuration={
            "language": "en",
            "buttons": {},
            "custom_buttons": [
                {
                    "id": index,
                    "label_en": f"Custom {index}",
                    "label_fa": f"سفارشی {index}",
                    "is_active": True,
                    "style": "primary",
                }
                for index in range(1, 10)
            ],
        },
    )
    labels = _button_texts(keyboard)

    assert [f"Custom {index}" for index in range(1, 10)] == [
        label for label in labels if label.startswith("Custom ")
    ]
    assert "🧩 More options" not in labels
    assert keyboard.is_persistent is True


def test_persistent_home_labels_resolve_without_fuzzy_matching():
    assert home_action_for_text("💎 خرید اشتراک") == "buy"
    assert home_action_for_text("👤 My subscription") == "subscription"
    assert home_action_for_text("🌐 Language | تغییر زبان") == "language"
    assert home_action_for_text("🌐 Change language") == "language"
    assert home_action_for_text("⚙️ پنل مدیریت") == "admin"
    assert home_action_for_text("https://example.com/video") is None


def test_payment_review_notifications_restore_the_home_keyboard():
    bot = SimpleNamespace(send_message=AsyncMock())
    message = SimpleNamespace(bot=bot)
    base_result = {
        "user": {
            "telegram_id": 123456789,
            "effective_language": "en",
            "is_admin": False,
        },
        "payment": {
            "id": 91,
            "plan_name_snapshot": "Premium",
            "duration_days": 45,
            "rejection_reason": "Test reason",
        },
        "subscription": {"expires_at": "2026-12-01T12:00:00+00:00"},
    }

    asyncio.run(_notify_user_approved(message, base_result))
    approved_markup = bot.send_message.await_args.kwargs["reply_markup"]

    asyncio.run(_notify_user_rejected(message, base_result))
    rejected_markup = bot.send_message.await_args.kwargs["reply_markup"]

    assert approved_markup.is_persistent is True
    assert rejected_markup.is_persistent is True
    assert _button_texts(approved_markup)[0] == "💎 Buy subscription"
    assert bot.send_message.await_count == 2


def test_start_installs_persistent_menu_for_admin(monkeypatch):
    async def fake_register(_message):
        return {"effective_language": "fa", "is_admin": True}

    monkeypatch.setattr(main_module, "register_telegram_user", fake_register)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123456789, language_code="fa"),
        chat=SimpleNamespace(type="private"),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    asyncio.run(main_module.start_handler(message, state))

    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.is_persistent is True
    assert _button_texts(markup)[-1] == "⚙️ پنل مدیریت"


def test_subscription_reply_button_routes_before_download(monkeypatch):
    async def fake_register(_message):
        return {"effective_language": "fa", "is_admin": False}

    subscription_status = AsyncMock()
    monkeypatch.setattr(home_handler, "register_telegram_user", fake_register)
    monkeypatch.setattr(
        home_handler,
        "send_subscription_status",
        subscription_status,
    )
    message = SimpleNamespace(
        text="👤 وضعیت اشتراک من",
        from_user=SimpleNamespace(id=123456789, language_code="fa"),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    asyncio.run(
        home_handler.persistent_home_button(
            message,
            state,
            {"action": "subscription"},
        )
    )

    state.clear.assert_awaited_once()
    subscription_status.assert_awaited_once_with(message, 123456789)


def test_menu_command_restores_the_persistent_bottom_keyboard(monkeypatch):
    async def fake_register(_message):
        return {"effective_language": "en", "is_admin": True}

    async def fake_runtime_configuration(_language):
        return {"language": "en", "buttons": {}, "custom_buttons": []}

    monkeypatch.setattr(home_handler, "register_telegram_user", fake_register)
    monkeypatch.setattr(
        home_handler,
        "runtime_configuration",
        fake_runtime_configuration,
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123456789, language_code="en"),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    asyncio.run(home_handler.restore_main_menu(message, state))

    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.is_persistent is True
    assert _button_texts(markup)[-1] == "⚙️ Admin panel"


def test_returning_from_payment_uses_reply_keyboard_not_inline_menu():
    message = SimpleNamespace(
        text="Payment screen",
        answer=AsyncMock(),
        delete=AsyncMock(),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )

    asyncio.run(
        _replace_payment_message(
            message,
            "Cancelled",
            reply_markup=build_home_reply_keyboard("en"),
        )
    )

    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.is_persistent is True
    message.delete.assert_awaited_once()
    message.edit_text.assert_not_awaited()


def test_telegram_command_menu_keeps_existing_commands_and_adds_menu():
    bot = SimpleNamespace(
        get_my_commands=AsyncMock(
            return_value=[SimpleNamespace(command="start")]
        ),
        set_my_commands=AsyncMock(),
        set_chat_menu_button=AsyncMock(),
    )

    asyncio.run(main_module.configure_telegram_menu_button(bot))

    commands = bot.set_my_commands.await_args.args[0]
    assert [command.command for command in commands] == ["start", "menu"]
    assert commands[-1].description == "Open main menu / باز کردن منوی اصلی"
    assert bot.set_chat_menu_button.await_args.kwargs["menu_button"].type == "commands"
