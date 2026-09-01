from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.admin_labels import permission_label_fa, role_label_fa


def build_admin_home_keyboard(
    permissions: set[str],
    *,
    is_superadmin: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if is_superadmin or "admins.manage" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👮 مدیریت مدیران",
                    callback_data="admin:accounts",
                )
            ]
        )

    if is_superadmin or "roles.manage" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔐 نقش‌ها و دسترسی‌ها",
                    callback_data="admin:roles",
                )
            ]
        )

    if is_superadmin or "settings.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚙️ تنظیمات ربات",
                    callback_data="admin:settings",
                )
            ]
        )

    if is_superadmin or "payments.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 مدیریت پرداخت‌ها",
                    callback_data="payment:open",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ بستن پنل",
                callback_data="admin:close",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل",
                    callback_data="admin:open",
                )
            ]
        ]
    )


def build_admin_accounts_keyboard(
    accounts: list[dict],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for account in accounts:
        status = "🟢" if account.get("is_active") else "⚫️"
        authority = "👑" if account.get("is_superadmin") else "👮"
        username = account.get("username")
        identity = (
            f"@{username}"
            if username
            else str(account.get("telegram_id"))
        )[:45]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {authority} {identity}",
                    callback_data=(
                        "admin:account:"
                        f"{int(account['telegram_id'])}"
                    ),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ افزودن مدیر",
                    callback_data="admin:account:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل",
                    callback_data="admin:open",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_account_detail_keyboard(
    account: dict,
) -> InlineKeyboardMarkup:
    telegram_id = int(account["telegram_id"])
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🔐 ویرایش نقش‌ها",
                callback_data=f"admin:account:roles:{telegram_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=(
                    "⬇️ لغو سوپرادمین"
                    if account.get("is_superadmin")
                    else "⬆️ ارتقا به سوپرادمین"
                ),
                callback_data=f"admin:account:super:{telegram_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=(
                    "⛔️ غیرفعال‌سازی"
                    if account.get("is_active")
                    else "✅ فعال‌سازی"
                ),
                callback_data=f"admin:account:status:{telegram_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 فهرست مدیران",
                callback_data="admin:accounts",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_role_picker_keyboard(
    roles: list[dict],
    selected_codes: set[str],
    *,
    allow_superadmin: bool,
    is_superadmin: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for role in roles:
        if not role.get("is_active"):
            continue

        code = str(role["code"])
        selected = code in selected_codes
        rows.append(
            [
                InlineKeyboardButton(
                    text=("✅ " if selected else "⬜️ ")
                    + role_label_fa(
                        code,
                        str(role.get("name") or code),
                    )[:45],
                    callback_data=f"admin:rolepick:{int(role['id'])}",
                )
            ]
        )

    if allow_superadmin:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        "👑 سوپرادمین: بله"
                        if is_superadmin
                        else "👑 سوپرادمین: خیر"
                    ),
                    callback_data="admin:rolepick:super",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="ادامه ✅",
                    callback_data="admin:rolepick:done",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data="admin:workflow:cancel",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_change_confirmation_keyboard(
    *,
    dangerous: bool,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "⚠️ ادامه برای تأیید نهایی"
                        if dangerous
                        else "✅ تأیید و ثبت"
                    ),
                    callback_data="admin:change:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data="admin:workflow:cancel",
                )
            ],
        ]
    )


def build_final_danger_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚨 بله، تغییر حساس ثبت شود",
                    callback_data="admin:change:final",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data="admin:workflow:cancel",
                )
            ],
        ]
    )


def build_admin_roles_keyboard(
    roles: list[dict],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for role in roles:
        status = "🟢" if role.get("is_active") else "⚫️"
        system = "🔒" if role.get("is_system") else "🧩"
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{status} {system} "
                        + role_label_fa(
                            str(role.get("code") or ""),
                            str(role.get("name") or role.get("code") or ""),
                        )[:45]
                    ),
                    callback_data=f"admin:role:{int(role['id'])}",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ ساخت نقش سفارشی",
                    callback_data="admin:role:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 بازگشت به پنل",
                    callback_data="admin:open",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_role_detail_keyboard(role: dict) -> InlineKeyboardMarkup:
    role_id = int(role["id"])
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✏️ تغییر نام",
                callback_data=f"admin:role:name:{role_id}",
            ),
            InlineKeyboardButton(
                text="📝 تغییر توضیح",
                callback_data=f"admin:role:description:{role_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🔐 ویرایش دسترسی‌ها",
                callback_data=f"admin:role:permissions:{role_id}",
            )
        ],
    ]

    if not role.get("is_system"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        "⛔️ غیرفعال‌سازی نقش"
                        if role.get("is_active")
                        else "✅ فعال‌سازی نقش"
                    ),
                    callback_data=f"admin:role:status:{role_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 فهرست نقش‌ها",
                callback_data="admin:roles",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_permission_picker_keyboard(
    permissions: list[dict],
    selected_codes: set[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for permission in permissions:
        code = str(permission["code"])
        selected = code in selected_codes
        rows.append(
            [
                InlineKeyboardButton(
                    text=("✅ " if selected else "⬜️ ")
                    + permission_label_fa(code)[:50],
                    callback_data=f"admin:permpick:{int(permission['id'])}",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="ادامه ✅",
                    callback_data="admin:permpick:done",
                )
            ],
            [
                InlineKeyboardButton(
                    text="انصراف",
                    callback_data="admin:workflow:cancel",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
