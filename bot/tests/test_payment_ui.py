from app.handlers.admin_finance import _payment_statistics_text
from app.handlers.payments import _status_caption, _subscription_status_text
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
    assert buttons[3].text == "🌐 Language | تغییر زبان"


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
        }
    )

    assert "امروز" in text
    assert "سال جاری" in text
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
