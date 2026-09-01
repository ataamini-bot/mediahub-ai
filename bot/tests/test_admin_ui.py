from app.handlers.admin import _backend_error_text, _can
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

    assert "آخرین Superadmin" in _backend_error_text(exc)


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
