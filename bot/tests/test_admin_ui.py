from app.handlers.admin import _can
from app.keyboards.admin import build_admin_home_keyboard


def test_superadmin_sees_all_foundation_menu_entries():
    keyboard = build_admin_home_keyboard(set(), is_superadmin=True)
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]

    assert callbacks == [
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
