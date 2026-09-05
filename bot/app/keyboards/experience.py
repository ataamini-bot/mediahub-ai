from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.runtime_config import runtime_button


SUPPORT_CATEGORY_LABELS = {
    "fa": {
        "financial": "💳 امور مالی و پرداخت",
        "technical": "🛠 مشکل فنی و دانلود",
        "account": "👤 حساب و اشتراک",
        "general": "💬 سایر موارد",
    },
    "en": {
        "financial": "💳 Payments and finance",
        "technical": "🛠 Technical and downloads",
        "account": "👤 Account and subscription",
        "general": "💬 Other questions",
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
        + [[InlineKeyboardButton(text="❌ انصراف", callback_data="support:cancel")]]
    )


def build_support_admin_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✍️ پاسخ",
                    callback_data=f"support_admin:reply:{ticket_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="✅ بستن تیکت",
                    callback_data=f"support_admin:close:{ticket_id}",
                    style="danger",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👁 مشاهده جزئیات",
                    callback_data=f"admin:support:ticket:{ticket_id}",
                )
            ],
        ]
    )


def build_support_ticket_list_keyboard(tickets: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    category_icon = {
        "financial": "💳",
        "technical": "🛠",
        "account": "👤",
        "general": "💬",
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
                    callback_data=f"admin:support:ticket:{ticket['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data="admin:support")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data="admin:open")])
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
                    text="✍️ پاسخ به کاربر",
                    callback_data=f"support_admin:reply:{ticket_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="✅ بستن",
                    callback_data=f"support_admin:close:{ticket_id}",
                    style="danger",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 فهرست تیکت‌ها", callback_data="admin:support")])
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

