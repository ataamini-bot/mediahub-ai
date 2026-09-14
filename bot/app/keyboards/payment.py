from app.localization import tr as _tr, localized_collection as _localized_collection
from decimal import Decimal, InvalidOperation
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.i18n import translate
from app.runtime_config import fallback_configuration, runtime_button, runtime_button_style


def _configuration(language: str, value: dict | None) -> dict:
    return value if isinstance(value, dict) else fallback_configuration(language)


def _reply_button(text: str, style: str = "default") -> KeyboardButton:
    kwargs = {"text": text}
    if style in {"primary", "success", "danger"}:
        kwargs["style"] = style
    return KeyboardButton(**kwargs)


def _inline_button(*, text: str, callback_data: str, style: str = "default") -> InlineKeyboardButton:
    kwargs = {"text": text, "callback_data": callback_data}
    if style in {"primary", "success", "danger"}:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)


def format_toman(value: object) -> str:
    try:
        amount = int(float(str(value)))
    except (TypeError, ValueError):
        return str(value)

    return f"{amount:,}{_tr(' تومان')}"


def format_usdt(value: object) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("Invalid amount")
        # Catalog prices support four decimal places. Never change the
        # amount a customer is instructed to transfer through display rounding.
        places = max(2, -amount.normalize().as_tuple().exponent)
        return f"{amount:.{places}f} USDT"
    except (InvalidOperation, TypeError, ValueError):
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
            _inline_button(text=runtime_button(config, "buy"), callback_data="payment:open", style=runtime_button_style(config, "buy"))
        ],
        [
            _inline_button(text=runtime_button(config, "subscription"), callback_data="payment:status", style=runtime_button_style(config, "subscription"))
        ],
        [
            _inline_button(text=runtime_button(config, "support"), callback_data="support:open", style=runtime_button_style(config, "support")),
            _inline_button(text=runtime_button(config, "language"), callback_data="language:open", style=runtime_button_style(config, "language")),
        ],
        [
            _inline_button(text=runtime_button(config, "convert"), callback_data="convert:open", style=runtime_button_style(config, "convert")),
        ],
        [
            InlineKeyboardButton(
                text=runtime_button(config, "tutorial"),
                callback_data="home:tutorial",
                **({"style": runtime_button_style(config, "tutorial")} if runtime_button_style(config, "tutorial") != "default" else {}),
            ),
            InlineKeyboardButton(
                text=runtime_button(config, "faq"),
                callback_data="home:faq",
                **({"style": runtime_button_style(config, "faq")} if runtime_button_style(config, "faq") != "default" else {}),
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
                    **({"style": runtime_button_style(config, "admin")} if runtime_button_style(config, "admin") != "default" else {}),
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
            _reply_button(runtime_button(config, "buy"), runtime_button_style(config, "buy")),
            _reply_button(runtime_button(config, "subscription"), runtime_button_style(config, "subscription")),
        ],
        [
            _reply_button(runtime_button(config, "support"), runtime_button_style(config, "support")),
            _reply_button(runtime_button(config, "language"), runtime_button_style(config, "language")),
        ],
        [
            _reply_button(runtime_button(config, "convert"), runtime_button_style(config, "convert")),
        ],
        [
            _reply_button(runtime_button(config, "tutorial"), runtime_button_style(config, "tutorial")),
            _reply_button(runtime_button(config, "faq"), runtime_button_style(config, "faq")),
        ],
    ]

    customs = [
        c
        for c in config.get("custom_buttons", [])
        if isinstance(c, dict) and c.get("is_active", True)
    ]
    custom_row: list[KeyboardButton] = []
    # Reply keyboards scroll vertically in Telegram. Keep every active
    # custom button here instead of replacing the seventh one with an
    # inline "More options" home menu, which created a duplicate main menu
    # inside the chat.
    for custom in customs:
        label = str(custom.get(f"label_{config.get('language', language)}") or "").strip()
        if not label:
            continue
        custom_row.append(_reply_button(label[:64], str(custom.get("style") or "default")))
        if len(custom_row) == 2:
            rows.append(custom_row); custom_row = []
    if custom_row:
        rows.append(custom_row)
    if include_admin:
        rows.append(
            [_reply_button(runtime_button(config, "admin"), runtime_button_style(config, "admin"))]
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
                        f"{offer['label']} — {int(offer['duration_days'])} {(_tr('روز') if is_fa else 'days')} — {(format_usdt(offer.get('price')) if offer.get('currency') == 'USDT' else format_toman(offer['price']))}"
                    ),
                    callback_data=f"payment:offer:{offer['code']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("❌ بستن") if is_fa else "❌ Close",
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
                    text=_tr("✅ ادامه پرداخت") if is_fa else "✅ Continue to payment",
                    callback_data="payment:offer:continue",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔙 بازگشت به پلن‌ها") if is_fa else "🔙 Back to plans",
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


def build_receipt_cancel_keyboard(language: str = "fa", order_id: str | None = None) -> InlineKeyboardMarkup:
    is_fa = language != "en"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        _tr("🔙 انتخاب پلن دیگر")
                        if is_fa
                        else "🔙 Choose another plan"
                    ),
                    callback_data=f"payment:order:change:{order_id}" if order_id else "payment:open",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("❌ انصراف") if is_fa else "❌ Cancel",
                    callback_data=f"payment:order:cancel:{order_id}" if order_id else "payment:cancel",
                )
            ],
        ]
    )


def build_admin_payment_keyboard(
    payment_id: int,
    *,
    explorer_url: str | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    if explorer_url:
        rows.append(
            [InlineKeyboardButton(text=_tr("🔎 مشاهده تراکنش"), url=explorer_url)]
        )
    rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("✅ تأیید و فعال‌سازی"),
                    callback_data=f"payment_admin:approve:{payment_id}",
                ),
                InlineKeyboardButton(
                    text=_tr("❌ رد رسید"),
                    callback_data=f"payment_admin:reject:{payment_id}",
                ),
            ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_usdt_screenshot_keyboard(order_id: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Submit without screenshot",
                    callback_data=f"payment:order:submit:{order_id}" if order_id else "payment:usdt-screenshot:skip",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"payment:order:cancel:{order_id}" if order_id else "payment:cancel",
                )
            ],
        ]
    )


def build_payment_approval_confirmation_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_tr("✅ تأیید نهایی و فعال‌سازی"),
                    callback_data=f"payment_admin:approve-confirm:{payment_id}",
                    style="danger",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
                    callback_data=f"payment_admin:approve-cancel:{payment_id}",
                )
            ],
        ]
    )
