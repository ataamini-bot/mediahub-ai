from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.i18n import translate
from app.runtime_config import fallback_configuration, runtime_button


def _configuration(language: str, value: dict | None) -> dict:
    return value if isinstance(value, dict) else fallback_configuration(language)


def _reply_button(text: str, style: str = "default") -> KeyboardButton:
    kwargs = {"text": text}
    if style in {"primary", "success", "danger"}:
        kwargs["style"] = style
    return KeyboardButton(**kwargs)


def format_toman(value: object) -> str:
    try:
        amount = int(float(str(value)))
    except (TypeError, ValueError):
        return str(value)

    return f"{amount:,} تومان"


def format_usdt(value: object) -> str:
    try:
        return f"{float(str(value)):.2f} USDT"
    except (TypeError, ValueError):
        return f"{value} USDT"


def format_usdt_network(destination: dict) -> str:
    """Build an English-only public label for a USDT network."""
    network_name = str(destination.get("network_name") or "").strip()
    network_code = str(destination.get("network_code") or "").strip()
    if not network_name.isascii():
        network_name = ""
    if not network_code.isascii():
        network_code = ""
    if (
        network_name
        and network_code
        and network_name.casefold() != network_code.casefold()
    ):
        return f"{network_name} ({network_code})"
    return network_name or network_code or "USDT network"


def build_home_keyboard(
    language: str = "fa",
    *,
    include_admin: bool = False,
    configuration: dict | None = None,
) -> InlineKeyboardMarkup:
    config = _configuration(language, configuration)
    rows = [
        [
            InlineKeyboardButton(
                text=runtime_button(config, "buy"),
                callback_data="payment:open",
                style="success",
            )
        ],
        [
            InlineKeyboardButton(
                text=runtime_button(config, "subscription"),
                callback_data="payment:status",
                style="primary",
            )
        ],
        [
            InlineKeyboardButton(
                text=runtime_button(config, "support"),
                callback_data="support:open",
                style="primary",
            ),
            InlineKeyboardButton(
                text=runtime_button(config, "language"),
                callback_data="language:open",
            )
        ],
        [
            InlineKeyboardButton(
                text=runtime_button(config, "tutorial"),
                callback_data="home:tutorial",
            ),
            InlineKeyboardButton(
                text=runtime_button(config, "faq"),
                callback_data="home:faq",
            ),
        ],
    ]

    for custom in config.get("custom_buttons", []):
        if not isinstance(custom, dict) or not custom.get("is_active", True):
            continue
        label = str(custom.get(f"label_{config.get('language', language)}") or "").strip()
        if not label:
            continue
        kwargs = {"text": label[:64]}
        style = str(custom.get("style") or "default")
        if style in {"primary", "success", "danger"}:
            kwargs["style"] = style
        if custom.get("action_type") == "url" and custom.get("action_value"):
            kwargs["url"] = str(custom["action_value"])
        else:
            kwargs["callback_data"] = f"home:custom:{int(custom['id'])}"
        rows.append([InlineKeyboardButton(**kwargs)])

    if include_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text=runtime_button(config, "admin"),
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
    configuration: dict | None = None,
) -> ReplyKeyboardMarkup:
    """Build the persistent menu displayed beside the Telegram input field."""
    config = _configuration(language, configuration)
    rows = [
        [
            _reply_button(runtime_button(config, "buy"), "success"),
            _reply_button(runtime_button(config, "subscription"), "primary"),
        ],
        [
            _reply_button(runtime_button(config, "support"), "primary"),
            _reply_button(runtime_button(config, "language")),
        ],
        [
            _reply_button(runtime_button(config, "tutorial")),
            _reply_button(runtime_button(config, "faq")),
        ],
    ]

    custom_row: list[KeyboardButton] = []
    for custom in config.get("custom_buttons", []):
        if not isinstance(custom, dict) or not custom.get("is_active", True):
            continue
        label = str(custom.get(f"label_{config.get('language', language)}") or "").strip()
        if not label:
            continue
        custom_row.append(_reply_button(label[:64], str(custom.get("style") or "default")))
        if len(custom_row) == 2:
            rows.append(custom_row)
            custom_row = []
    if custom_row:
        rows.append(custom_row)

    if include_admin:
        rows.append(
            [_reply_button(runtime_button(config, "admin"), "danger")]
        )

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder=translate(language, "home.placeholder"),
    )


def build_upgrade_keyboard(
    language: str = "fa",
) -> InlineKeyboardMarkup:
    """Offer the subscription flow directly below plan-limit errors."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=translate(language, "home.buy"),
                    callback_data="payment:open",
                    style="success",
                )
            ]
        ]
    )


def build_payment_offers_keyboard(
    offers: list[dict],
    language: str = "fa",
) -> InlineKeyboardMarkup:
    rows = []
    is_fa = language != "en"

    for offer in offers:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{offer['label']} — {int(offer['duration_days'])} "
                        f"{'روز' if is_fa else 'days'} — "
                        f"{format_usdt(offer.get('price')) if offer.get('currency') == 'USDT' else format_toman(offer['price'])}"
                    ),
                    callback_data=f"payment:offer:{offer['code']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ بستن" if is_fa else "❌ Close",
                callback_data="payment:cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_payment_offer_detail_keyboard(language: str = "fa") -> InlineKeyboardMarkup:
    is_fa = language != "en"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ ادامه پرداخت" if is_fa else "✅ Continue to payment",
                    callback_data="payment:offer:continue",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پلن‌ها" if is_fa else "🔙 Back to plans",
                    callback_data="payment:open",
                )
            ],
        ]
    )


def build_usdt_destination_keyboard(
    destinations: list[dict],
) -> InlineKeyboardMarkup:
    rows = []
    for destination in destinations:
        network_label = format_usdt_network(destination)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🌐 {network_label}"[:64],
                    callback_data=(
                        "payment:usdt-destination:"
                        f"{int(destination['id'])}"
                    ),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="🔙 Back to plans",
                    callback_data="payment:open",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="payment:cancel",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_receipt_cancel_keyboard(language: str = "fa") -> InlineKeyboardMarkup:
    is_fa = language != "en"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "🔙 انتخاب پلن دیگر"
                        if is_fa
                        else "🔙 Choose another plan"
                    ),
                    callback_data="payment:open",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف" if is_fa else "❌ Cancel",
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
