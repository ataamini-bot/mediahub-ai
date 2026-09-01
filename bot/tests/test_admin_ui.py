import asyncio
from types import SimpleNamespace

from app.handlers import admin as admin_handler
from app.handlers.admin import (
    _admin_panel_text,
    _admin_role_text,
    _backend_error_text,
    _can,
)
from app.keyboards.admin import (
    build_admin_account_detail_keyboard,
    build_admin_home_keyboard,
    build_role_picker_keyboard,
)
from app.services.backend import BackendAPIError


def test_superadmin_sees_all_foundation_menu_entries():
    keyboard = build_admin_home_keyboard(set(), is_superadmin=True)
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]

    assert callbacks == [
        "admin:accounts",
        "admin:roles",
        "admin:settings",
        "payment:open",
        "admin:close",
    ]


def test_permission_helper_accepts_role_permission():
    context = {
        "is_superadmin": False,
        "permissions": ["settings.view"],
    }

    assert _can(context, "settings.view") is True
    assert _can(context, "settings.manage") is False


def test_role_picker_supports_multiple_role_selection():
    roles = [
        {
            "id": 1,
            "code": "support",
            "name": "Support",
            "is_active": True,
        },
        {
            "id": 2,
            "code": "finance",
            "name": "Finance",
            "is_active": True,
        },
    ]
    keyboard = build_role_picker_keyboard(
        roles,
        {"support", "finance"},
        allow_superadmin=True,
        is_superadmin=False,
    )
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]

    assert callbacks[:2] == ["admin:rolepick:1", "admin:rolepick:2"]
    assert keyboard.inline_keyboard[0][0].text.startswith("✅")
    assert keyboard.inline_keyboard[1][0].text.startswith("✅")
    assert "admin:rolepick:super" in callbacks
    assert "admin:rolepick:done" in callbacks


def test_last_superadmin_error_has_safe_persian_message():
    exc = BackendAPIError(
        status_code=409,
        detail={
            "code": "last_superadmin",
            "message": "internal message",
        },
    )

    assert "آخرین سوپرادمین" in _backend_error_text(exc)


def test_account_detail_exposes_soft_deactivation_not_delete():
    keyboard = build_admin_account_detail_keyboard(
        {
            "telegram_id": 123,
            "is_superadmin": False,
            "is_active": True,
        }
    )
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]

    assert "admin:account:status:123" in callbacks
    assert not any("delete" in callback for callback in callbacks)



def test_system_roles_and_permissions_are_rendered_in_persian():
    panel_text = _admin_panel_text(
        {
            "is_superadmin": False,
            "roles": ["payment_finance"],
            "permissions": ["payments.view"],
        }
    )
    role_text = _admin_role_text(
        {
            "code": "support",
            "name": "Support Admin",
            "description": "Built-in Support Admin role",
            "is_system": True,
            "is_active": True,
            "assignment_count": 1,
            "permission_codes": ["tickets.view", "tickets.reply"],
        }
    )

    assert "مدیر پرداخت و امور مالی" in panel_text
    assert "مدیر پشتیبانی" in role_text
    assert "مشاهده تیکت‌های پشتیبانی" in role_text
    assert "پاسخ‌گویی به تیکت‌ها" in role_text
    assert "Support Admin" not in role_text


def test_role_and_permission_pickers_use_persian_labels():
    roles = [
        {
            "id": 1,
            "code": "support",
            "name": "Support Admin",
            "is_active": True,
        }
    ]
    role_keyboard = build_role_picker_keyboard(
        roles,
        {"support"},
        allow_superadmin=False,
        is_superadmin=False,
    )

    assert "مدیر پشتیبانی" in role_keyboard.inline_keyboard[0][0].text


def test_unauthorized_admin_command_is_silent(monkeypatch):
    answers: list[str] = []

    async def fake_register(_message):
        return None

    async def fake_context(_telegram_id):
        return None

    async def answer(text, **_kwargs):
        answers.append(text)

    monkeypatch.setattr(
        admin_handler,
        "register_telegram_user",
        fake_register,
    )
    monkeypatch.setattr(
        admin_handler,
        "_context_or_none",
        fake_context,
    )

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=987654321),
        answer=answer,
    )
    asyncio.run(
        admin_handler.admin_command(
            message,
            SimpleNamespace(),
        )
    )

    assert answers == []
