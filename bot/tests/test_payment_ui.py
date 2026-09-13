from app.handlers.admin_finance import _payment_statistics_text
from app.handlers.payments import _status_caption, _subscription_status_text
from app.keyboards.payment import (
    build_admin_payment_keyboard,
    build_home_keyboard,
    build_home_reply_keyboard,
    build_payment_offers_keyboard,
    build_usdt_destination_keyboard,
    format_usdt_network,
    format_toman,
)
from app.main import download_error_text
from app.services.backend import BackendAPIError


def test_format_toman():
    assert format_toman("79000.00") == "79,000 تومان"


def test_home_keyboard_supports_language_and_admin_entry():
    keyboard = build_home_keyboard(language="en", include_admin=True)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert [button.callback_data for button in buttons] == [
        "payment:open",
        "payment:status",
        "support:open",
        "language:open",
        "home:tutorial",
        "home:faq",
        "admin:open",
    ]
    assert buttons[3].text == "🌐 Language"


def test_main_button_colors_are_read_from_runtime_configuration():
    configuration = {
        "language": "en",
        "buttons": {
            "buy": "Buy", "subscription": "Status", "support": "Support",
            "language": "Language", "tutorial": "How to use", "faq": "FAQ", "admin": "Admin",
        },
        "button_styles": {
            "buy": "danger", "subscription": "success", "support": "default",
            "language": "primary", "tutorial": "danger", "faq": "success", "admin": "primary",
        },
        "custom_buttons": [],
    }
    inline = build_home_keyboard(language="en", include_admin=True, configuration=configuration)
    inline_by_callback = {button.callback_data: button for row in inline.inline_keyboard for button in row}
    assert inline_by_callback["payment:open"].style == "danger"
    assert inline_by_callback["payment:status"].style == "success"
    assert inline_by_callback["language:open"].style == "primary"
    reply = build_home_reply_keyboard(language="en", include_admin=True, configuration=configuration)
    reply_by_text = {button.text: button for row in reply.keyboard for button in row}
    assert reply_by_text["Buy"].style == "danger"
    assert reply_by_text["Admin"].style == "primary"


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


def test_offer_keyboard_is_english_for_usdt_catalog():
    keyboard = build_payment_offers_keyboard(
        [
            {
                "code": "plan_global",
                "label": "Global",
                "duration_days": 30,
                "price": "2.7500",
                "currency": "USDT",
            }
        ],
        language="en",
    )

    assert "30 days" in keyboard.inline_keyboard[0][0].text
    assert "USDT" in keyboard.inline_keyboard[0][0].text
    assert keyboard.inline_keyboard[-1][0].text == "❌ Close"


def test_usdt_destination_keyboard_lists_every_network_in_order():
    keyboard = build_usdt_destination_keyboard(
        [
            {"id": 11, "network_name": "TRON", "network_code": "TRC20"},
            {
                "id": 12,
                "network_name": "Ethereum",
                "network_code": "ERC20",
            },
        ]
    )

    assert [row[0].text for row in keyboard.inline_keyboard[:2]] == [
        "🌐 TRON (TRC20)",
        "🌐 Ethereum (ERC20)",
    ]
    assert [row[0].callback_data for row in keyboard.inline_keyboard[:2]] == [
        "payment:usdt-destination:11",
        "payment:usdt-destination:12",
    ]
    assert all(
        "انصراف" not in button.text
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_usdt_network_label_does_not_leak_persian_admin_text():
    assert format_usdt_network(
        {"network_name": "شبکه ترون", "network_code": "TRC20"}
    ) == "TRC20"


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


def test_payment_statistics_separate_toman_and_usdt_totals():
    text = _payment_statistics_text(
        {
            "statistics": {
                period: {
                    "approved": 2,
                    "pending": 1,
                    "rejected": 0,
                    "irt_total": 158000,
                    "usdt_total": "5.5000",
                }
                for period in ("daily", "weekly", "monthly", "yearly", "all")
            }
        },
        "daily",
    )

    assert "امروز" in text
    assert "سال جاری" not in text
    assert "158,000 تومان" in text
    assert "5.50 USDT" in text


def test_persian_subscription_uses_plan_label_and_effective_quota():
    text = _subscription_status_text(
        {
            "is_active": True,
            "plan_name": "نقره‌ای",
            "duration_days": 30,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "registered_at": "2026-01-01T00:00:00+00:00",
            "downloads_done": 12,
            "daily_download_limit": 100,
            "remaining_downloads": 88,
        },
        "fa",
    )

    assert "💎 پلن:" in text
    assert "💎 Plan:" not in text
    assert "محدودیت دانلود روزانه: <code>100</code>" in text
    assert "دانلود باقیمانده: <code>88</code>" in text


def test_english_subscription_uses_english_plan_name():
    text = _subscription_status_text(
        {
            "is_active": True,
            "plan_name": "نقره‌ای",
            "plan_name_en": "Silver",
            "duration_days": 30,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "registered_at": "2026-01-01T00:00:00+00:00",
            "downloads_done": 12,
            "daily_download_limit": 100,
            "remaining_downloads": 88,
        },
        "en",
    )

    assert "💎 Plan: <b>Silver</b>" in text
    assert "نقره‌ای" not in text


def test_weekly_free_limit_error_is_explicit():
    error = BackendAPIError(
        status_code=429,
        detail={
            "code": "weekly_download_limit_reached",
            "plan_name": "Free",
            "used": 3,
            "limit": 3,
        },
    )
    assert "سهمیه هفتگی" in download_error_text(error)
