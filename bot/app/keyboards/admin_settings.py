from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.admin_runtime_settings import (
    RUNTIME_SETTINGS,
    RUNTIME_SETTINGS_BY_KEY,
    setting_value_text,
)


def build_runtime_settings_keyboard(
    rows: list[dict],
    *,
    can_manage: bool,
    can_manage_channels: bool = False,
) -> InlineKeyboardMarkup:
    rows_by_key = {str(row.get("key")): row for row in rows}
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    if can_manage:
        keyboard_rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="📝 متن‌ها و عنوان دکمه‌ها",
                        callback_data="admin:copy",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🧩 دکمه‌های سفارشی صفحه اصلی",
                        callback_data="admin:homebuttons",
                    )
                ],
            ]
        )

    if can_manage_channels:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="📢 عضویت اجباری کانال‌ها",
                    callback_data="admin:channels",
                )
            ]
        )

    if can_manage:
        for definition in RUNTIME_SETTINGS:
            row = rows_by_key.get(definition.key)
            if row is None:
                continue
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"{definition.label}: "
                            f"{setting_value_text(definition, row.get('value'))}"
                        )[:60],
                        callback_data=f"admin:setting:edit:{definition.key}",
                    )
                ]
            )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت به پنل",
                callback_data="admin:open",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def build_setting_value_keyboard(key: str) -> InlineKeyboardMarkup:
    definition = RUNTIME_SETTINGS_BY_KEY[key]
    rows: list[list[InlineKeyboardButton]] = []

    if definition.kind == "timezone":
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🇮🇷 تهران",
                        callback_data="admin:setting:zone:tehran",
                    ),
                    InlineKeyboardButton(
                        text="🌐 UTC",
                        callback_data="admin:setting:zone:utc",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🇦🇪 دبی",
                        callback_data="admin:setting:zone:dubai",
                    )
                ],
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="انصراف",
                callback_data="admin:setting:cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_setting_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید و اعمال",
                    callback_data="admin:setting:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data="admin:setting:cancel",
                )
            ],
        ]
    )
