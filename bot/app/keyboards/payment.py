from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def format_toman(value: object) -> str:
    try:
        amount = int(float(str(value)))
    except (TypeError, ValueError):
        return str(value)

    return f"{amount:,} تومان"


def build_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 خرید اشتراک",
                    callback_data="payment:open",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 وضعیت اشتراک من",
                    callback_data="payment:status",
                )
            ],
        ]
    )


def build_payment_offers_keyboard(
    offers: list[dict],
) -> InlineKeyboardMarkup:
    rows = []

    for offer in offers:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{offer['label']} — "
                        f"{format_toman(offer['price'])}"
                    ),
                    callback_data=f"payment:offer:{offer['code']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ بستن",
                callback_data="payment:cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_receipt_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 انتخاب پلن دیگر",
                    callback_data="payment:open",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data="payment:cancel",
                )
            ],
        ]
    )


def build_admin_payment_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید و فعال‌سازی",
                    callback_data=f"payment_admin:approve:{payment_id}",
                ),
                InlineKeyboardButton(
                    text="❌ رد رسید",
                    callback_data=f"payment_admin:reject:{payment_id}",
                ),
            ]
        ]
    )
