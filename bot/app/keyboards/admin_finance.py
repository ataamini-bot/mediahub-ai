from app.localization import tr as _tr, localized_collection as _localized_collection
from math import ceil

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.keyboards.payment import format_toman, format_usdt


PAYMENT_PAGE_SIZE = 8
PAYMENT_STATISTICS_PERIODS = {
    "daily": "امروز",
    "weekly": "۷ روز اخیر",
    "monthly": "ماه جاری (میلادی)",
    "yearly": "سال جاری (میلادی)",
    "all": "کل دوره",
}


def build_payment_statistics_keyboard(
    *,
    choose_period: bool = True,
) -> InlineKeyboardMarkup:
    if choose_period:
        rows = [
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"admin:pay:stats:{period}",
                )
            ]
            for period, label in PAYMENT_STATISTICS_PERIODS.items()
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text=_tr("📅 انتخاب بازهٔ دیگر"),
                    callback_data="admin:pay:stats",
                )
            ]
        ]
    rows.append([InlineKeyboardButton(text=_tr("📊 گزارش تفصیلی و CSV"), callback_data="reports:finance")])
    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("🔙 مدیریت پرداخت‌ها"),
                callback_data="admin:payments",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_finance_home_keyboard(
    *,
    can_manage_destinations: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=_tr("⏳ رسیدهای در انتظار"),
                callback_data="admin:pay:list:pending:1",
            )
        ],
        [
            InlineKeyboardButton(
                text=_tr("🧾 همه پرداخت‌ها"),
                callback_data="admin:pay:list:all:1",
            )
        ],
        [
            InlineKeyboardButton(
                text=_tr("📊 آمار پرداخت‌ها"),
                callback_data="admin:pay:stats",
            )
        ],
    ]
    if can_manage_destinations:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=_tr("💳 شماره کارت‌ها"),
                        callback_data="admin:cards",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_tr("💵 کیف‌پول‌های USDT"),
                        callback_data="admin:usdt",
                    )
                ],
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("🔙 بازگشت به پنل"),
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
                        f"{identity[:18]} — "
                        f"{format_usdt(payment['amount']) if payment.get('payment_method') == 'usdt' else format_toman(payment['amount'])}"
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
                text=_tr("◀️ قبلی"),
                callback_data=f"admin:pay:list:{status_filter}:{page - 1}",
            )
        )
    if page < page_count:
        navigation.append(
            InlineKeyboardButton(
                text=_tr("بعدی ▶️"),
                callback_data=f"admin:pay:list:{status_filter}:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("🔙 مدیریت پرداخت‌ها"),
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
                    text=_tr("✅ تأیید و فعال‌سازی"),
                    callback_data=f"payment_admin:approve:{payment['id']}",
                ),
                InlineKeyboardButton(
                    text=_tr("❌ رد رسید"),
                    callback_data=f"payment_admin:reject:{payment['id']}",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("🔙 فهرست پرداخت‌ها"),
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
                        f"{status} {str(card.get('label') or _tr('کارت'))[:28]} — **** {number[-4:]}"
                    ),
                    callback_data=f"admin:card:{int(card['id'])}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=_tr("➕ افزودن کارت"),
                    callback_data="admin:card:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔙 مدیریت پرداخت‌ها"),
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
                    text=_tr("✏️ عنوان"),
                    callback_data=f"admin:card:edit:label:{card_id}",
                ),
                InlineKeyboardButton(
                    text=_tr("💳 شماره کارت"),
                    callback_data=f"admin:card:edit:number:{card_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("👤 صاحب کارت"),
                    callback_data=f"admin:card:edit:holder:{card_id}",
                ),
                InlineKeyboardButton(
                    text=_tr("🏦 نام بانک"),
                    callback_data=f"admin:card:edit:bank:{card_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("↕️ ترتیب"),
                    callback_data=f"admin:card:edit:order:{card_id}",
                ),
                InlineKeyboardButton(
                    text=(
                        _tr("⛔️ غیرفعال‌سازی")
                        if card.get("is_active")
                        else _tr("✅ فعال‌سازی")
                    ),
                    callback_data=f"admin:card:toggle:{card_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🗑 حذف کارت"),
                    callback_data=f"admin:card:delete:{card_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔙 فهرست کارت‌ها"),
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
                    text=_tr("➕ افزودن کیف‌پول USDT"),
                    callback_data="admin:usdt:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔙 مدیریت پرداخت‌ها"),
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
                InlineKeyboardButton(text=_tr("✏️ عنوان"), callback_data=callback("label")),
                InlineKeyboardButton(
                    text=_tr("🌐 نام شبکه"),
                    callback_data=callback("network_name"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔤 کد شبکه"),
                    callback_data=callback("network_code"),
                ),
                InlineKeyboardButton(
                    text=_tr("📍 آدرس"),
                    callback_data=callback("address"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔢 تأییدها"),
                    callback_data=callback("confirmations_required"),
                ),
                InlineKeyboardButton(
                    text=_tr("↕️ ترتیب"),
                    callback_data=callback("sort_order"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🧾 قرارداد"),
                    callback_data=callback("contract_address"),
                ),
                InlineKeyboardButton(
                    text=_tr("🔎 مرورگر شبکه"),
                    callback_data=callback("explorer_url"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        _tr("⛔️ غیرفعال‌سازی")
                        if destination.get("is_active")
                        else _tr("✅ فعال‌سازی")
                    ),
                    callback_data=f"admin:usdt:toggle:{destination_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🗑 حذف کیف‌پول"),
                    callback_data=f"admin:usdt:delete:{destination_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔙 فهرست کیف‌پول‌ها"),
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
                    text=_tr("انصراف"),
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
                    text=_tr("✅ تأیید و ثبت"),
                    callback_data="admin:finance:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
                    callback_data="admin:finance:cancel",
                )
            ],
        ]
    )


# Resolve static labels using the language of the current update.
PAYMENT_STATISTICS_PERIODS = _localized_collection(PAYMENT_STATISTICS_PERIODS)
