from math import ceil

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.keyboards.payment import format_toman


PAYMENT_PAGE_SIZE = 8


def build_finance_home_keyboard(
    *,
    can_manage_destinations: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="⏳ رسیدهای در انتظار",
                callback_data="admin:pay:list:pending:1",
            )
        ],
        [
            InlineKeyboardButton(
                text="🧾 همه پرداخت‌ها",
                callback_data="admin:pay:list:all:1",
            )
        ],
    ]
    if can_manage_destinations:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="💳 شماره کارت‌ها",
                        callback_data="admin:cards",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💵 کیف‌پول‌های USDT",
                        callback_data="admin:usdt",
                    )
                ],
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل",
                callback_data="admin:open",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_payment_list_keyboard(
    items: list[dict],
    *,
    status_filter: str,
    page: int,
    total: int,
) -> InlineKeyboardMarkup:
    status_icons = {
        "pending": "⏳",
        "approved": "✅",
        "rejected": "❌",
    }
    rows: list[list[InlineKeyboardButton]] = []
    for payment in items:
        username = payment.get("username")
        identity = (
            f"@{username}"
            if username
            else str(payment.get("user_telegram_id") or "—")
        )
        status = str(payment.get("status") or "")
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{status_icons.get(status, '•')} #{payment['id']} "
                        f"{identity[:18]} — {format_toman(payment['amount'])}"
                    )[:60],
                    callback_data=(
                        f"admin:pay:view:{int(payment['id'])}:"
                        f"{status_filter}:{page}"
                    ),
                )
            ]
        )

    page_count = max(1, ceil(total / PAYMENT_PAGE_SIZE))
    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="◀️ قبلی",
                callback_data=f"admin:pay:list:{status_filter}:{page - 1}",
            )
        )
    if page < page_count:
        navigation.append(
            InlineKeyboardButton(
                text="بعدی ▶️",
                callback_data=f"admin:pay:list:{status_filter}:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 مدیریت پرداخت‌ها",
                callback_data="admin:payments",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_payment_detail_keyboard(
    payment: dict,
    *,
    can_review: bool,
    status_filter: str,
    page: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_review and payment.get("status") == "pending":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ تأیید و فعال‌سازی",
                    callback_data=f"payment_admin:approve:{payment['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ رد رسید",
                    callback_data=f"payment_admin:reject:{payment['id']}",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 فهرست پرداخت‌ها",
                callback_data=f"admin:pay:list:{status_filter}:{page}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_cards_keyboard(cards: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for card in cards:
        status = "🟢" if card.get("is_active") else "⚫️"
        number = str(card.get("card_number") or "")
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{status} {str(card.get('label') or 'کارت')[:28]} "
                        f"— **** {number[-4:]}"
                    ),
                    callback_data=f"admin:card:{int(card['id'])}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ افزودن کارت",
                    callback_data="admin:card:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 مدیریت پرداخت‌ها",
                    callback_data="admin:payments",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_card_detail_keyboard(card: dict) -> InlineKeyboardMarkup:
    card_id = int(card["id"])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ عنوان",
                    callback_data=f"admin:card:edit:label:{card_id}",
                ),
                InlineKeyboardButton(
                    text="💳 شماره کارت",
                    callback_data=f"admin:card:edit:number:{card_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 صاحب کارت",
                    callback_data=f"admin:card:edit:holder:{card_id}",
                ),
                InlineKeyboardButton(
                    text="🏦 نام بانک",
                    callback_data=f"admin:card:edit:bank:{card_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↕️ ترتیب",
                    callback_data=f"admin:card:edit:order:{card_id}",
                ),
                InlineKeyboardButton(
                    text=(
                        "⛔️ غیرفعال‌سازی"
                        if card.get("is_active")
                        else "✅ فعال‌سازی"
                    ),
                    callback_data=f"admin:card:toggle:{card_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 حذف کارت",
                    callback_data=f"admin:card:delete:{card_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 فهرست کارت‌ها",
                    callback_data="admin:cards",
                )
            ],
        ]
    )


def build_usdt_keyboard(destinations: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for destination in destinations:
        status = "🟢" if destination.get("is_active") else "⚫️"
        address = str(destination.get("address") or "")
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{status} {str(destination.get('network_code') or 'USDT')} "
                        f"— …{address[-6:]}"
                    )[:60],
                    callback_data=f"admin:usdt:item:{int(destination['id'])}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ افزودن کیف‌پول USDT",
                    callback_data="admin:usdt:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 مدیریت پرداخت‌ها",
                    callback_data="admin:payments",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_usdt_detail_keyboard(destination: dict) -> InlineKeyboardMarkup:
    destination_id = int(destination["id"])

    def callback(field: str) -> str:
        return f"admin:usdt:edit:{field}:{destination_id}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ عنوان", callback_data=callback("label")),
                InlineKeyboardButton(
                    text="🌐 نام شبکه",
                    callback_data=callback("network_name"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔤 کد شبکه",
                    callback_data=callback("network_code"),
                ),
                InlineKeyboardButton(
                    text="📍 آدرس",
                    callback_data=callback("address"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔢 تأییدها",
                    callback_data=callback("confirmations_required"),
                ),
                InlineKeyboardButton(
                    text="↕️ ترتیب",
                    callback_data=callback("sort_order"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧾 قرارداد",
                    callback_data=callback("contract_address"),
                ),
                InlineKeyboardButton(
                    text="🔎 مرورگر شبکه",
                    callback_data=callback("explorer_url"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "⛔️ غیرفعال‌سازی"
                        if destination.get("is_active")
                        else "✅ فعال‌سازی"
                    ),
                    callback_data=f"admin:usdt:toggle:{destination_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 حذف کیف‌پول",
                    callback_data=f"admin:usdt:delete:{destination_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 فهرست کیف‌پول‌ها",
                    callback_data="admin:usdt",
                )
            ],
        ]
    )


def build_finance_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data="admin:finance:cancel",
                )
            ]
        ]
    )


def build_finance_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید و ثبت",
                    callback_data="admin:finance:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data="admin:finance:cancel",
                )
            ],
        ]
    )
