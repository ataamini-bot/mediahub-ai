from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


CONTENT_LABELS = {
    "welcome_title": "عنوان خوش‌آمدگویی",
    "welcome_instruction": "راهنمای کوتاه صفحه شروع",
    "tutorial": "آموزش استفاده از ربات",
    "faq": "سوالات متداول",
    "support_intro": "متن انتخاب موضوع پشتیبانی",
    "support_prompt": "راهنمای ارسال پیام پشتیبانی",
    "support_sent": "تأیید ثبت درخواست پشتیبانی",
    "forced_join": "پیام عضویت اجباری",
    "membership_verified": "پیام تأیید عضویت",
}

BUTTON_LABELS = {
    "buy": "خرید اشتراک",
    "subscription": "وضعیت اشتراک",
    "language": "تغییر زبان",
    "support": "پشتیبانی",
    "tutorial": "آموزش استفاده",
    "faq": "سوالات متداول",
    "admin": "پنل مدیریت",
    "check_membership": "بررسی عضویت",
    "back_home": "منوی اصلی",
}


def build_copy_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇮🇷 فارسی", callback_data="admin:copy:lang:fa"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="admin:copy:lang:en"),
            ],
            [InlineKeyboardButton(text="🔙 تنظیمات ربات", callback_data="admin:settings")],
        ]
    )


def build_copy_section_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 متن محتواها",
                    callback_data=f"admin:copy:section:{language}:content",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔘 عنوان دکمه‌ها",
                    callback_data=f"admin:copy:section:{language}:buttons",
                )
            ],
            [InlineKeyboardButton(text="🔙 انتخاب زبان", callback_data="admin:copy")],
        ]
    )


def build_copy_items_keyboard(language: str, section: str) -> InlineKeyboardMarkup:
    labels = CONTENT_LABELS if section == "content" else BUTTON_LABELS
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"admin:copy:item:{language}:{section}:{key}",
            )
        ]
        for key, label in labels.items()
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 بازگشت",
                callback_data=f"admin:copy:lang:{language}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_copy_cancel_keyboard(language: str, section: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data=f"admin:copy:section:{language}:{section}",
                )
            ]
        ]
    )


def build_home_buttons_admin_keyboard(buttons: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for button in buttons:
        status = "🟢" if button.get("is_active") else "⚫️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {str(button.get('label_fa') or 'بدون نام')[:50]}",
                    callback_data=f"admin:homebutton:{button['id']}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="➕ افزودن دکمه", callback_data="admin:homebutton:add")],
            [InlineKeyboardButton(text="🔙 تنظیمات ربات", callback_data="admin:settings")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_home_button_detail_keyboard(button: dict) -> InlineKeyboardMarkup:
    button_id = int(button["id"])
    toggle = "⛔️ غیرفعال‌کردن" if button.get("is_active") else "✅ فعال‌کردن"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ عنوان فارسی",
                    callback_data=f"admin:homebutton:edit:fa:{button_id}",
                ),
                InlineKeyboardButton(
                    text="✏️ عنوان انگلیسی",
                    callback_data=f"admin:homebutton:edit:en:{button_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎨 تغییر رنگ",
                    callback_data=f"admin:homebutton:style:{button_id}",
                ),
                InlineKeyboardButton(
                    text="🔗 تغییر عملکرد",
                    callback_data=f"admin:homebutton:action:{button_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=toggle,
                    callback_data=f"admin:homebutton:toggle:{button_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 حذف دکمه",
                    callback_data=f"admin:homebutton:deleteask:{button_id}",
                    style="danger",
                )
            ],
            [InlineKeyboardButton(text="🔙 فهرست دکمه‌ها", callback_data="admin:homebuttons")],
        ]
    )


def build_home_action_keyboard(*, button_id: int | None = None) -> InlineKeyboardMarkup:
    suffix = str(button_id) if button_id is not None else "new"
    actions = [
        ("🔗 بازکردن لینک", "url"),
        ("📝 نمایش متن", "message"),
        ("💎 خرید اشتراک", "buy"),
        ("👤 وضعیت اشتراک", "subscription"),
        ("🛟 پشتیبانی", "support"),
        ("📘 آموزش", "tutorial"),
        ("❓ سوالات متداول", "faq"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"admin:hbaction:{suffix}:{action}",
            )
        ]
        for label, action in actions
    ]
    rows.append([InlineKeyboardButton(text="انصراف", callback_data="admin:homebuttons")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_home_style_keyboard(*, button_id: int | None = None) -> InlineKeyboardMarkup:
    suffix = str(button_id) if button_id is not None else "new"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="معمولی", callback_data=f"admin:hbstyle:{suffix}:default"),
                InlineKeyboardButton(
                    text="آبی",
                    callback_data=f"admin:hbstyle:{suffix}:primary",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="سبز",
                    callback_data=f"admin:hbstyle:{suffix}:success",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="قرمز",
                    callback_data=f"admin:hbstyle:{suffix}:danger",
                    style="danger",
                ),
            ],
            [InlineKeyboardButton(text="انصراف", callback_data="admin:homebuttons")],
        ]
    )


def build_home_button_delete_keyboard(button_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="بله، حذف شود",
                    callback_data=f"admin:homebutton:delete:{button_id}",
                    style="danger",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data=f"admin:homebutton:{button_id}",
                )
            ],
        ]
    )


def build_channels_admin_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for channel in channels:
        status = "🟢" if channel.get("is_active") else "⚫️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {str(channel.get('title') or 'بدون نام')[:50]}",
                    callback_data=f"admin:channel:{channel['id']}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="➕ افزودن کانال", callback_data="admin:channel:add")],
            [InlineKeyboardButton(text="🔙 تنظیمات ربات", callback_data="admin:settings")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_channel_detail_keyboard(channel: dict) -> InlineKeyboardMarkup:
    channel_id = int(channel["id"])
    toggle = "⛔️ غیرفعال‌کردن" if channel.get("is_active") else "✅ فعال‌کردن"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle,
                    callback_data=f"admin:channel:toggle:{channel_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 حذف کانال",
                    callback_data=f"admin:channel:deleteask:{channel_id}",
                    style="danger",
                )
            ],
            [InlineKeyboardButton(text="🔙 فهرست کانال‌ها", callback_data="admin:channels")],
        ]
    )


def build_channel_delete_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="بله، حذف شود",
                    callback_data=f"admin:channel:delete:{channel_id}",
                    style="danger",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data=f"admin:channel:{channel_id}",
                )
            ],
        ]
    )

