from app.admin_runtime_settings import runtime_settings_text
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


def test_settings_viewer_only_gets_back_button():
    keyboard = build_runtime_settings_keyboard(
        sample_settings(),
        can_manage=False,
    )

    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].callback_data == "admin:open"
