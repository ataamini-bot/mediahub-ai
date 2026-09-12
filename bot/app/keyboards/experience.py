from app.localization import tr as _tr, localized_collection as _localized_collection
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.runtime_config import runtime_button


SUPPORT_CATEGORY_LABELS = {
    "fa": {
        "download": "📥 دانلود",
        "payment": "💳 پرداخت",
        "subscription": "💎 اشتراک",
        "account": "👤 حساب و اشتراک",
        "other": "💬 سایر موارد",
    },
    "en": {
        "download": "📥 Downloads",
        "payment": "💳 Payments",
        "subscription": "💎 Subscription",
        "account": "👤 Account",
        "other": "💬 Other questions",
    },
}


def build_support_categories_keyboard(language: str = "fa") -> InlineKeyboardMarkup:
    labels = SUPPORT_CATEGORY_LABELS.get(language, SUPPORT_CATEGORY_LABELS["fa"])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"support:category:{code}",
                )
            ]
            for code, label in labels.items()
        ]
        + [[InlineKeyboardButton(
            text=_tr("🗂 تاریخچه تیکت‌های من") if language != "en" else "🗂 My tickets",
            callback_data="support:history:1",
        )], [InlineKeyboardButton(
            text=_tr("❌ انصراف") if language != "en" else "❌ Cancel",
            callback_data="support:cancel",
        )]]
    )


def build_support_admin_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_tr("✍️ پاسخ"),
                    callback_data=f"support_admin:reply:{ticket_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=_tr("✅ بستن تیکت"),
                    callback_data=f"support_admin:close:{ticket_id}",
                    style="danger",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("👁 مشاهده جزئیات"),
                    callback_data=f"admin:support:ticket:{ticket_id}",
                )
            ],
        ]
    )


def build_support_ticket_list_keyboard(
    tickets: list[dict],
    *,
    page: int = 1,
    total: int | None = None,
    status: str = "all",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    category_icon = {
        "download": "📥",
        "payment": "💳",
        "subscription": "💎",
        "account": "👤",
        "other": "💬",
    }
    for ticket in tickets:
        user = ticket.get("user") or {}
        identity = user.get("username") or user.get("telegram_id") or "—"
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{category_icon.get(ticket.get('category'), '💬')} "
                        f"#{ticket['id']} — {identity}"
                    )[:64],
                    callback_data=f"admin:support:ticket:{ticket['id']}:{status}:{page}",
                )
            ]
        )
    page_total = max(1, ((total if total is not None else len(tickets)) + 7) // 8)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text=_tr("◀️ قبلی"), callback_data=f"admin:support:list:{status}:{page - 1}"))
    if page < page_total:
        nav.append(InlineKeyboardButton(text=_tr("بعدی ▶️"), callback_data=f"admin:support:list:{status}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=_tr("🔎 فیلتر وضعیت"), callback_data="admin:support:filters")])
    rows.append([InlineKeyboardButton(text=_tr("🔄 تازه‌سازی"), callback_data=f"admin:support:list:{status}:{page}")])
    rows.append([InlineKeyboardButton(text=_tr("🔙 بازگشت به پنل"), callback_data="admin:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_support_ticket_detail_keyboard(
    ticket_id: int,
    *,
    is_closed: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not is_closed:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("✍️ پاسخ به کاربر"),
                    callback_data=f"support_admin:reply:{ticket_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=_tr("✅ بستن"),
                    callback_data=f"support_admin:close:{ticket_id}",
                    style="danger",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text=_tr("⏳ در حال بررسی"), callback_data=f"support_admin:status:in_progress:{ticket_id}"),
                InlineKeyboardButton(text=_tr("👤 منتظر کاربر"), callback_data=f"support_admin:status:waiting_user:{ticket_id}"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text=_tr("☑️ پاسخ‌داده‌شده"), callback_data=f"support_admin:status:answered:{ticket_id}"),
                InlineKeyboardButton(text=_tr("↪️ ارجاع"), callback_data=f"support_admin:assign:{ticket_id}"),
            ]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text=_tr("♻️ بازگشایی تیکت"), callback_data=f"support_admin:reopen:{ticket_id}", style="success")]
        )
    rows.append([InlineKeyboardButton(text=_tr("🗂 همه پیام‌ها و پیوست‌ها"), callback_data=f"ticketlog:a:{ticket_id}:1")])
    rows.append([InlineKeyboardButton(text=_tr("🔙 فهرست تیکت‌ها"), callback_data="admin:support:list:all:1")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_support_status_filters_keyboard() -> InlineKeyboardMarkup:
    labels = {
        "all": _tr("همه"),
        "new": _tr("جدید"),
        "in_progress": _tr("در حال بررسی"),
        "waiting_user": _tr("منتظر کاربر"),
        "answered": _tr("پاسخ‌داده‌شده"),
        "closed": _tr("بسته"),
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"admin:support:list:{status}:1")]
            for status, label in labels.items()
        ]
    )


def build_user_ticket_list_keyboard(
    tickets: list[dict], *, page: int, total: int, language: str
) -> InlineKeyboardMarkup:
    status_labels = {
        "new": "New",
        "in_progress": "In progress",
        "waiting_user": "Waiting for you",
        "answered": "Answered",
        "closed": "Closed",
    }
    status_labels_fa = {
        "new": "جدید",
        "in_progress": "در حال بررسی",
        "waiting_user": "منتظر کاربر",
        "answered": "پاسخ‌داده‌شده",
        "closed": "بسته",
    }
    labels = status_labels if language == "en" else status_labels_fa
    rows = [
        [InlineKeyboardButton(
            text=f"🎫 #{ticket['id']} — {labels.get(str(ticket.get('status')), 'Unknown' if language == 'en' else 'نامشخص')}"[:64],
            callback_data=f"support:view:{ticket['id']}:{page}",
        )]
        for ticket in tickets
    ]
    pages = max(1, (total + 7) // 8)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"support:history:{page - 1}"))
    if page < pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"support:history:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Back" if language == "en" else _tr("🔙 بازگشت"), callback_data="support:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_user_ticket_detail_keyboard(
    ticket_id: int, *, is_closed: bool, page: int, language: str
) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(
        text="🗂 All messages and attachments" if language == "en" else _tr("🗂 همه پیام‌ها و پیوست‌ها"),
        callback_data=f"ticketlog:u:{ticket_id}:1",
    )])
    if not is_closed:
        rows.append([InlineKeyboardButton(
            text="✍️ Add reply" if language == "en" else _tr("✍️ افزودن پاسخ"),
            callback_data=f"support:reply:{ticket_id}",
            style="success",
        )])
    rows.append([InlineKeyboardButton(
        text="🔙 My tickets" if language == "en" else _tr("🔙 تاریخچه تیکت‌ها"),
        callback_data=f"support:history:{page}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_required_membership_keyboard(
    configuration: dict,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(channel["title"])[:64], url=channel["invite_url"])]
        for channel in configuration.get("required_channels", [])
        if channel.get("invite_url")
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=runtime_button(configuration, "check_membership"),
                callback_data="membership:check",
                style="success",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_custom_url_keyboard(label: str, url: str, style: str) -> InlineKeyboardMarkup:
    kwargs = {"text": label[:64], "url": url}
    if style in {"primary", "success", "danger"}:
        kwargs["style"] = style
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(**kwargs)]])


# Resolve static labels using the language of the current update.
SUPPORT_CATEGORY_LABELS = _localized_collection(SUPPORT_CATEGORY_LABELS)
