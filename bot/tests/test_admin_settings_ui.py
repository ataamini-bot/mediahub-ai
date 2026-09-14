from app.admin_runtime_settings import runtime_settings_text
from app.main import dp
from app.keyboards.admin_experience import (
    BUTTON_LABELS,
    build_home_buttons_root_keyboard,
)
from app.keyboards.admin_settings import build_runtime_settings_keyboard


def sample_settings() -> list[dict]:
    return [
        {
            "key": "bot.maintenance_mode",
            "category": "bot",
            "value": False,
            "version": 1,
        },
        {
            "key": "downloads.enabled",
            "category": "downloads",
            "value": True,
            "version": 1,
        },
        {
            "key": "payments.enabled",
            "category": "payments",
            "value": True,
            "version": 1,
        },
        {
            "key": "payments.receipt_max_size_mb",
            "category": "payments",
            "value": 12,
            "version": 1,
        },
        {
            "key": "quota.timezone",
            "category": "quota",
            "value": "Asia/Tehran",
            "version": 1,
        },
    ]


def test_runtime_settings_are_rendered_with_persian_labels():
    text = runtime_settings_text(sample_settings())

    assert "حالت تعمیرات" in text
    assert "دانلود برای کاربران" in text
    assert "حداکثر حجم رسید" in text
    assert "12 مگابایت" in text
    assert "bot.maintenance_mode" not in text


def test_settings_manager_gets_edit_buttons():
    keyboard = build_runtime_settings_keyboard(
        sample_settings(),
        can_manage=True,
    )
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]

    assert "admin:setting:edit:bot.maintenance_mode" in callbacks
    assert "admin:setting:edit:payments.receipt_max_size_mb" in callbacks
    assert callbacks[-1] == "admin:open"


def test_home_buttons_are_the_parent_for_labels_and_custom_button_management():
    keyboard = build_runtime_settings_keyboard(
        sample_settings(),
        can_manage=True,
    )
    callbacks = [row[0].callback_data for row in keyboard.inline_keyboard]

    assert callbacks[0] == "admin:homebuttons"
    assert "admin:copy" not in callbacks

    home_keyboard = build_home_buttons_root_keyboard()
    home_callbacks = [row[0].callback_data for row in home_keyboard.inline_keyboard]
    assert home_callbacks == [
        "admin:copy",
        "admin:homebuttons:list",
        "admin:settings",
    ]


def test_button_editor_contains_every_builtin_home_button():
    assert {
        "buy",
        "subscription",
        "support",
        "language",
        "convert",
        "tutorial",
        "faq",
        "admin",
    }.issubset(BUTTON_LABELS.keys())


def test_settings_viewer_only_gets_back_button():
    keyboard = build_runtime_settings_keyboard(
        sample_settings(),
        can_manage=False,
    )

    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].callback_data == "admin:open"


def test_experience_admin_handlers_are_registered():
    routers = {router.name: router for router in dp.sub_routers}

    assert "admin-experience" in routers
    assert len(routers["admin-experience"].callback_query.handlers) >= 20
    assert len(routers["admin-experience"].message.handlers) >= 8
