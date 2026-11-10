"""Bilingual coupon amounts and actionable errors shared by checkout and admin."""
import html
import re

from app.middleware.interface import ui_language
from app.keyboards.payment import format_toman, format_usdt


def tr(fa, en, language=None):
    return en if (language or ui_language.get()) == "en" else fa


def normalize_code(value):
    code = str(value).strip().upper().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,31}", code):
        raise ValueError("coupon_code_invalid")
    return code


def discount_text(snapshot, language=None):
    if not snapshot:
        return ""
    money = format_usdt if snapshot.get("currency") == "USDT" else format_toman
    return (
        f"\n🎟 {tr('کد تخفیف', 'Discount code', language)}: <code>{html.escape(snapshot['code'])}</code>\n"
        f"{tr('مبلغ اولیه', 'Original amount', language)}: {money(snapshot['original_price'])}\n"
        f"{tr('تخفیف', 'Discount', language)}: −{money(snapshot['discount_amount'])}\n"
        f"{tr('مبلغ نهایی', 'Final amount', language)}: <b>{money(snapshot['final_price'])}</b>"
    )


def coupon_error(exc, language=None):
    detail = getattr(exc, "detail", {})
    code = detail.get("code") if isinstance(detail, dict) else str(detail)
    errors = {
        "coupon_not_found": ("کد برای این ارز پیدا نشد.", "This code is not available for this currency."),
        "coupon_inactive": ("کد غیرفعال است.", "This code is disabled."),
        "coupon_not_started": ("زمان استفاده از کد هنوز شروع نشده است.", "This code is not valid yet."),
        "coupon_expired": ("کد منقضی شده است.", "This code has expired."),
        "coupon_total_limit": ("ظرفیت کد مصرف یا برای سفارش‌های دیگر رزرو شده است.", "This code's capacity has been used or reserved by other orders."),
        "coupon_user_limit": ("سقف استفادهٔ شما از این کد پر شده است.", "You have reached this code's usage limit."),
        "coupon_plan_scope": ("کد برای این پلن قابل استفاده نیست.", "This code does not apply to this plan."),
        "coupon_duration_scope": ("کد برای مدت این پلن قابل استفاده نیست.", "This code does not apply to this plan duration."),
        "coupon_zero_total": ("این کد مبلغ سفارش را صفر می‌کند؛ مبلغ پرداخت باید مثبت بماند.", "This code would make the total zero. A positive payment amount is required."),
        "coupon_no_discount": ("پس از گردکردن مبلغ، تخفیفی ایجاد نمی‌شود.", "This code produces no discount after currency rounding."),
        "coupon_code_invalid": ("کد باید ۳ تا ۳۲ حرف انگلیسی، عدد، خط تیره یا زیرخط باشد.", "Use a 3–32 character code with letters, digits, hyphens or underscores."),
        "coupon_stale": ("تنظیمات کد تغییر کرده است؛ دوباره باز کنید.", "The code has changed. Reopen it before editing."),
        "coupon_conflict": ("این کد برای همین ارز وجود دارد یا درخواست تکراری ناسازگار است.", "This code already exists for this currency or the request conflicts."),
        "coupon_limit_below_usage": ("سقف جدید از تعداد مصرف و رزرو فعلی کمتر است.", "The new limit is below existing redemptions and reservations."),
        "coupon_offer_unavailable": ("این پلن دیگر قابل خرید نیست؛ پلن را دوباره انتخاب کنید.", "This plan is no longer available. Choose a plan again."),
        "coupon_request_reused": ("فرم تغییر کرده است؛ دوباره باز کنید.", "This request changed. Reopen the form."),
        "coupon_reservation_closed": ("رزرو این سفارش بسته شده است؛ وضعیت سفارش را دوباره بررسی کنید.", "This order's reservation is closed. Check the order status again."),
        "payment_order_language": ("ارز سفارش با زبان فعلی هماهنگ نیست؛ پلن را دوباره انتخاب کنید.", "Choose a plan again in your current language."),
    }
    if code in errors:
        return tr(*errors[code], language)
    return tr("ثبت انجام نشد؛ دسترسی و مقادیر فرم را بررسی کنید.", "Could not save. Check access and the form values.", language)
