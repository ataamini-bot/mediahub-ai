from app.localization import tr as _tr, localized_collection as _localized_collection
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_statistics_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_tr("👥 کاربران"), callback_data="admin:stats:users"), InlineKeyboardButton(text=_tr("💎 اشتراک"), callback_data="admin:stats:subscriptions")],
        [InlineKeyboardButton(text=_tr("💰 مالی"), callback_data="admin:stats:finance"), InlineKeyboardButton(text=_tr("📥 دانلود"), callback_data="admin:stats:downloads")],
        [InlineKeyboardButton(text=_tr("📈 نمودارها"), callback_data="admin:stats:charts"), InlineKeyboardButton(text=_tr("🎯 شاخص‌ها"), callback_data="admin:stats:kpis")],
        [InlineKeyboardButton(text=_tr("🔄 به‌روزرسانی"), callback_data="admin:statistics")],
        [InlineKeyboardButton(text=_tr("🔙 بازگشت به پنل"), callback_data="admin:open")],
    ])


STATISTICS_PERIODS = (("1yr", "۱ سال"), ("6mo", "۶ ماه"), ("3mo", "۳ ماه"), ("1mo", "۱ ماه"), ("7d", "۷ روز"), ("today", "روزانه"))


def build_statistics_section_keyboard(section: str, *, period="1mo", page=1, total=0) -> InlineKeyboardMarkup:
    rows = []
    if section in {"downloads", "finance"}:
        for offset in (0, 3):
            rows.append([InlineKeyboardButton(text=("✓ " if key == period else "") + label,
                callback_data=f"admin:stats:{section}:{key}") for key, label in STATISTICS_PERIODS[offset:offset + 3]])
        if section == "downloads":
            nav = []
            if page > 1:
                nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:stats:downloads:{period}:{page - 1}"))
            if page * 8 < total:
                nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:stats:downloads:{period}:{page + 1}"))
            if nav: rows.append(nav)
        else:
            rows.append([InlineKeyboardButton(text=_tr("📊 گزارش تفصیلی و CSV"), callback_data="reports:finance")])
    elif section == "charts":
        for offset in (0, 3):
            rows.append([InlineKeyboardButton(text=("✓ " if key == period else "") + label,
                callback_data=f"admin:stats:charts:{key}") for key, label in STATISTICS_PERIODS[offset:offset + 3]])
        rows.append([InlineKeyboardButton(text=_tr("کاربران"), callback_data=f"admin:stats:charts:metric:users:{period}"), InlineKeyboardButton(text=_tr("فروش"), callback_data=f"admin:stats:charts:metric:sales:{period}"), InlineKeyboardButton(text=_tr("اشتراک"), callback_data=f"admin:stats:charts:metric:subscriptions:{period}"), InlineKeyboardButton(text=_tr("دانلود"), callback_data=f"admin:stats:charts:metric:downloads:{period}")])
    rows.extend([
        [InlineKeyboardButton(text=_tr("🔄 به‌روزرسانی"), callback_data=f"admin:stats:{section}:{period}:{page}" if section == "downloads" else f"admin:stats:{section}:{period}" if section == "charts" else f"admin:stats:{section}:{period}")],
        [InlineKeyboardButton(text=_tr("📊 منوی آمار"), callback_data="admin:statistics")],
        [InlineKeyboardButton(text=_tr("🔙 بازگشت به پنل"), callback_data="admin:open")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Resolve static labels using the language of the current update.
STATISTICS_PERIODS = _localized_collection(STATISTICS_PERIODS)
