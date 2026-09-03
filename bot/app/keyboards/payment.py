from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.i18n import translate


def format_toman(value: object) -> str:
    try:
        amount = int(float(str(value)))
    except (TypeError, ValueError):
        return str(value)

    return f"{amount:,} تومان"


def build_home_keyboard(
    language: str = "fa",
    *,
    include_admin: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=translate(language, "home.buy"),
                callback_data="payment:open",
            )
        ],
        [
            InlineKeyboardButton(
                text=translate(language, "home.subscription"),
                callback_data="payment:status",
            )
        ],
        [
            InlineKeyboardButton(
                text=translate(language, "home.language"),
                callback_data="language:open",
            )
        ],
    ]

    if include_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text=translate(language, "home.admin"),
                    callback_data="admin:open",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def build_home_reply_keyboard(
    language: str = "fa",
    *,
    include_admin: bool = False,
) -> ReplyKeyboardMarkup:
    """Build the persistent menu displayed beside the Telegram input field."""
    rows = [
        [
            KeyboardButton(text=translate(language, "home.buy")),
            KeyboardButton(text=translate(language, "home.subscription")),
        ],
        [KeyboardButton(text=translate(language, "home.language"))],
    ]

    if include_admin:
        rows.append(
            [KeyboardButton(text=translate(language, "home.admin"))]
        )

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder=translate(language, "home.placeholder"),
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
                        f"{offer['label']} — {int(offer['duration_days'])} روز — "
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
