from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_statistics_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 کاربران", callback_data="admin:stats:users"), InlineKeyboardButton(text="💎 اشتراک", callback_data="admin:stats:subscriptions")],
        [InlineKeyboardButton(text="💰 مالی", callback_data="admin:stats:finance"), InlineKeyboardButton(text="📥 دانلود", callback_data="admin:stats:downloads")],
        [InlineKeyboardButton(text="📈 نمودارها", callback_data="admin:stats:charts"), InlineKeyboardButton(text="🎯 شاخص‌ها", callback_data="admin:stats:kpis")],
        [InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data="admin:statistics")],
        [InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data="admin:open")],
    ])


def build_statistics_section_keyboard(section: str) -> InlineKeyboardMarkup:
    rows = []
    if section != "charts":
        rows.append([InlineKeyboardButton(text="امروز", callback_data=f"admin:stats:{section}:today"), InlineKeyboardButton(text="۷ روز", callback_data=f"admin:stats:{section}:7d"), InlineKeyboardButton(text="۳۰ روز", callback_data=f"admin:stats:{section}:30d"), InlineKeyboardButton(text="کلی", callback_data=f"admin:stats:{section}:all")])
    else:
        rows.append([InlineKeyboardButton(text="امروز", callback_data="admin:stats:charts:today"), InlineKeyboardButton(text="۷ روز", callback_data="admin:stats:charts:7d"), InlineKeyboardButton(text="۳۰ روز", callback_data="admin:stats:charts:30d")])
        rows.append([InlineKeyboardButton(text="۳ ماه", callback_data="admin:stats:charts:3mo"), InlineKeyboardButton(text="۱ سال", callback_data="admin:stats:charts:1yr")])
        rows.append([InlineKeyboardButton(text="کاربران", callback_data="admin:stats:charts:metric:users"), InlineKeyboardButton(text="فروش", callback_data="admin:stats:charts:metric:sales"), InlineKeyboardButton(text="اشتراک", callback_data="admin:stats:charts:metric:subscriptions"), InlineKeyboardButton(text="دانلود", callback_data="admin:stats:charts:metric:downloads")])
    rows.extend([[InlineKeyboardButton(text="📊 منوی آمار", callback_data="admin:statistics")], [InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data="admin:open")]])
    return InlineKeyboardMarkup(inline_keyboard=rows)
