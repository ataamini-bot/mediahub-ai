ROLE_LABELS_FA: dict[str, str] = {
    "payment_finance": "مدیر پرداخت و امور مالی",
    "user_subscription": "مدیر کاربران و اشتراک‌ها",
    "support": "مدیر پشتیبانی",
    "broadcast_content": "مدیر پیام‌های همگانی و محتوا",
    "technical_monitoring": "مدیر فنی و مانیتورینگ",
}

ROLE_DESCRIPTIONS_FA: dict[str, str] = {
    "payment_finance": (
        "مدیریت پرداخت‌ها، مقصدهای پرداخت، پلن‌ها، موجودی و تخفیف‌ها"
    ),
    "user_subscription": "مدیریت کاربران و اشتراک‌های آن‌ها",
    "support": "مشاهده، پاسخ‌گویی و مدیریت تیکت‌های پشتیبانی",
    "broadcast_content": "مدیریت پیام‌های همگانی و محتوای ارسالی",
    "technical_monitoring": (
        "مدیریت تنظیمات فنی، مانیتورینگ، بکاپ و گزارش فعالیت‌ها"
    ),
}

PERMISSION_LABELS_FA: dict[str, str] = {
    "admin.access": "ورود به پنل مدیریت",
    "admins.manage": "مدیریت مدیران",
    "roles.manage": "مدیریت نقش‌ها و دسترسی‌ها",
    "settings.view": "مشاهده تنظیمات ربات",
    "settings.manage": "ویرایش تنظیمات ربات",
    "payments.view": "مشاهده پرداخت‌ها و رسیدها",
    "payments.review": "تأیید یا رد پرداخت‌ها",
    "payment_destinations.manage": "مدیریت کارت‌ها و مقصدهای پرداخت",
    "plans.manage": "مدیریت پلن‌ها، قیمت‌ها و محدودیت‌ها",
    "users.view": "مشاهده و جست‌وجوی کاربران",
    "users.manage": "مدیریت کاربران",
    "subscriptions.manage": "مدیریت اشتراک‌ها",
    "balances.manage": "افزایش یا کاهش موجودی کاربران",
    "coupons.manage": "مدیریت کدهای تخفیف",
    "tickets.view": "مشاهده تیکت‌های پشتیبانی",
    "tickets.reply": "پاسخ‌گویی به تیکت‌ها",
    "tickets.manage": "مدیریت و ارجاع تیکت‌ها",
    "broadcasts.manage": "مدیریت پیام‌های همگانی",
    "forced_join.manage": "مدیریت عضویت اجباری",
    "monitoring.view": "مشاهده وضعیت مانیتورینگ",
    "monitoring.manage": "مدیریت مانیتورینگ و هشدارها",
    "backups.view": "مشاهده وضعیت و فهرست بکاپ‌ها",
    "backups.manage": "ایجاد و تنظیم بکاپ‌ها",
    "backups.restore": "بازیابی بکاپ رمزگذاری‌شده",
    "audit.view": "مشاهده گزارش فعالیت مدیران",
}


def role_label_fa(code: str, fallback: str | None = None) -> str:
    normalized = str(code or "").strip()
    return ROLE_LABELS_FA.get(normalized) or str(
        fallback or normalized or "بدون نقش"
    )


def role_description_fa(
    code: str,
    fallback: str | None = None,
) -> str:
    normalized = str(code or "").strip()
    return ROLE_DESCRIPTIONS_FA.get(normalized) or str(fallback or "—")


def permission_label_fa(code: str) -> str:
    normalized = str(code or "").strip()
    return PERMISSION_LABELS_FA.get(normalized) or normalized
