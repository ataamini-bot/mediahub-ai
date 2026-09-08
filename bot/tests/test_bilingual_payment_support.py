from app.handlers.payments import _offer_details_text, _payment_error_message
from app.keyboards.experience import build_support_categories_keyboard
from app.keyboards.payment import build_payment_offer_detail_keyboard
from app.runtime_config import fallback_configuration, runtime_content
from app.services.backend import BackendAPIError


def test_english_support_and_payment_controls_have_no_persian_text():
    support = build_support_categories_keyboard("en")
    assert all("انصراف" not in button.text for row in support.inline_keyboard for button in row)
    detail = build_payment_offer_detail_keyboard("en")
    assert all("پرداخت" not in button.text for row in detail.inline_keyboard for button in row)


def test_english_payment_errors_and_offer_details_are_english():
    error = _payment_error_message(BackendAPIError(status_code=503, detail="missing"), "en")
    assert "سیستم" not in error and "USDT" in error
    text = _offer_details_text({
        "label": "Pro", "duration_days": 30, "price": "2.5", "currency": "USDT",
        "description": "Fast downloads", "daily_download_limit": None,
        "max_file_size_mb": 1000, "max_quality": 1080, "max_concurrent_downloads": 2,
        "priority_processing": True, "forced_join_required": False,
    }, "en")
    assert "Duration" in text and "توضیح" not in text


def test_persian_runtime_copy_cannot_leak_into_english():
    configuration = fallback_configuration("en")
    configuration["content"]["support_sent"] = "شناسه پیگیری"
    assert "شناسه" not in runtime_content(configuration, "support_sent")
