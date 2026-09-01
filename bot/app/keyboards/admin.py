from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_admin_home_keyboard(
    permissions: set[str],
    *,
    is_superadmin: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if is_superadmin or "settings.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚙️ تنظیمات ربات",
                    callback_data="admin:settings",
                )
            ]
        )

    if is_superadmin or "payments.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 مدیریت پرداخت‌ها",
                    callback_data="payment:open",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ بستن پنل",
                callback_data="admin:close",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل",
                    callback_data="admin:open",
                )
            ]
        ]
    )
