from app.handlers.payments import _status_caption
from app.keyboards.payment import (
    build_admin_payment_keyboard,
    build_home_keyboard,
    build_payment_offers_keyboard,
    format_toman,
)


def test_format_toman():
    assert format_toman("79000.00") == "79,000 تومان"


def test_home_keyboard_supports_language_and_admin_entry():
    keyboard = build_home_keyboard(language="en", include_admin=True)
    buttons = [row[0] for row in keyboard.inline_keyboard]

    assert [button.callback_data for button in buttons] == [
        "payment:open",
        "payment:status",
        "language:open",
        "admin:open",
    ]
    assert buttons[2].text == "🌐 Change language"


def test_offer_keyboard_supports_arbitrary_custom_plan_durations():
    offers = [
        {
            "code": "plan_45_days",
            "label": "ویژه ۴۵ روزه",
            "duration_days": 45,
            "price": 125000,
        },
        {
            "code": "plan_120_days",
            "label": "حرفه‌ای",
            "duration_days": 120,
            "price": 300000,
        }
    ]

    keyboard = build_payment_offers_keyboard(offers)
    callbacks = [
        row[0].callback_data
        for row in keyboard.inline_keyboard[:-1]
    ]

    assert callbacks == [
        "payment:offer:plan_45_days",
        "payment:offer:plan_120_days",
    ]
    assert "45 روز" in keyboard.inline_keyboard[0][0].text


def test_admin_callback_data_stays_within_telegram_limit():
    keyboard = build_admin_payment_keyboard(9223372036854775807)

    for button in keyboard.inline_keyboard[0]:
        assert button.callback_data is not None
        assert len(button.callback_data.encode("utf-8")) <= 64


def test_status_caption_replaces_pending_status():
    original = "🧾 رسید\n\n⏳ وضعیت: <b>در انتظار بررسی</b>"

    result = _status_caption(original, "✅ وضعیت: <b>تأیید شد</b>")

    assert "در انتظار بررسی" not in result
    assert "تأیید شد" in result
    assert len(result) <= 1024
