from app.localization import tr as _tr, localized_collection as _localized_collection
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.admin_labels import permission_label_fa, role_label_fa
from app.utils.formatting import format_quality_limit


def build_admin_home_keyboard(
    permissions: set[str],
    *,
    is_superadmin: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if is_superadmin or permissions & {"admins.manage", "roles.manage"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("👮 مدیریت مدیران"),
                    callback_data="admin:accounts",
                )
            ]
        )

    if is_superadmin or "settings.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("⚙️ تنظیمات ربات"),
                    callback_data="admin:settings",
                )
            ]
        )

    if is_superadmin or "plans.manage" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("📦 مدیریت پلن‌ها"),
                    callback_data="admin:plans",
                )
            ]
        )

    if is_superadmin or "payments.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("💳 مدیریت پرداخت‌ها"),
                    callback_data="admin:payments",
                )
            ]
        )

    if is_superadmin or "statistics.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("📊 آمار ربات"),
                    callback_data="admin:statistics",
                )
            ]
        )

    if is_superadmin or "tickets.view" in permissions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("🛟 پشتیبانی کاربران"),
                    callback_data="admin:support",
                )
            ]
        )

    if is_superadmin or "monitoring.view" in permissions:
        rows.append([InlineKeyboardButton(text=_tr("🖥 مانیتورینگ و Topicها"), callback_data="ops:open")])
    if is_superadmin or "audit.view" in permissions:
        rows.append([InlineKeyboardButton(text=_tr("📜 فعالیت مدیران و Audit"), callback_data="reports:audit")])
    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("❌ بستن پنل"),
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
                    text=_tr("🔙 بازگشت به پنل"),
                    callback_data="admin:open",
                )
            ]
        ]
    )


def build_admin_accounts_keyboard(
    accounts: list[dict],
    *,
    can_manage_accounts: bool = True,
    can_manage_roles: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if can_manage_roles:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("🔐 نقش‌ها و دسترسی‌ها"),
                    callback_data="admin:roles",
                )
            ]
        )

    for account in accounts if can_manage_accounts else []:
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

    if can_manage_accounts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_tr("➕ افزودن مدیر"),
                    callback_data="admin:account:add",
                )
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


def build_admin_account_detail_keyboard(
    account: dict,
) -> InlineKeyboardMarkup:
    telegram_id = int(account["telegram_id"])
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=_tr("🔐 ویرایش نقش‌ها"),
                callback_data=f"admin:account:roles:{telegram_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=(
                    _tr("⬇️ لغو سوپرادمین")
                    if account.get("is_superadmin")
                    else _tr("⬆️ ارتقا به سوپرادمین")
                ),
                callback_data=f"admin:account:super:{telegram_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=(
                    _tr("⛔️ غیرفعال‌سازی")
                    if account.get("is_active")
                    else _tr("✅ فعال‌سازی")
                ),
                callback_data=f"admin:account:status:{telegram_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=_tr("🔙 فهرست مدیران"),
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
                        _tr("👑 سوپرادمین: بله")
                        if is_superadmin
                        else _tr("👑 سوپرادمین: خیر")
                    ),
                    callback_data="admin:rolepick:super",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=_tr("ادامه ✅"),
                    callback_data="admin:rolepick:done",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
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
                        _tr("⚠️ ادامه برای تأیید نهایی")
                        if dangerous
                        else _tr("✅ تأیید و ثبت")
                    ),
                    callback_data="admin:change:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
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
                    text=_tr("🚨 بله، تغییر حساس ثبت شود"),
                    callback_data="admin:change:final",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
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
                    text=_tr("➕ ساخت نقش سفارشی"),
                    callback_data="admin:role:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔙 مدیریت مدیران"),
                    callback_data="admin:accounts",
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
                text=_tr("✏️ تغییر نام"),
                callback_data=f"admin:role:name:{role_id}",
            ),
            InlineKeyboardButton(
                text=_tr("📝 تغییر توضیح"),
                callback_data=f"admin:role:description:{role_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=_tr("🔐 ویرایش دسترسی‌ها"),
                callback_data=f"admin:role:permissions:{role_id}",
            )
        ],
    ]

    if not role.get("is_system"):
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        _tr("⛔️ غیرفعال‌سازی نقش")
                        if role.get("is_active")
                        else _tr("✅ فعال‌سازی نقش")
                    ),
                    callback_data=f"admin:role:status:{role_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("🔙 فهرست نقش‌ها"),
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
                    text=_tr("ادامه ✅"),
                    callback_data="admin:permpick:done",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
                    callback_data="admin:workflow:cancel",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_plans_keyboard(plans: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for plan in plans:
        duration = (
            _tr("همیشگی")
            if plan.get("is_system")
            else f"{int(plan.get('duration_days', 0))}{_tr(' روز')}"
        )
        status = "" if plan.get("is_active") else _tr(" [غیرفعال]")
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{str(plan.get('name') or _tr('بدون نام'))[:32]}{status} — {duration}"
                    ),
                    callback_data=f"admin:plan:{int(plan['id'])}",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=_tr("➕ ایجاد پلن جدید"),
                    callback_data="admin:plan:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🔙 بازگشت به پنل"),
                    callback_data="admin:open",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_plan_detail_keyboard(plan: dict) -> InlineKeyboardMarkup:
    plan_id = int(plan["id"])
    rows: list[list[InlineKeyboardButton]] = []

    if not plan.get("is_system"):
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=_tr("✏️ نام فارسی"),
                        callback_data=f"admin:plan:edit:name:{plan_id}",
                    ),
                    InlineKeyboardButton(
                        text=_tr("🌐 نام انگلیسی"),
                        callback_data=f"admin:plan:edit:name_en:{plan_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=_tr("📝 توضیح فارسی"),
                        callback_data=f"admin:plan:edit:description:{plan_id}",
                    ),
                    InlineKeyboardButton(
                        text=_tr("🌐 توضیح انگلیسی"),
                        callback_data=f"admin:plan:edit:description_en:{plan_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=_tr("📅 مدت"),
                        callback_data=f"admin:plan:edit:duration:{plan_id}",
                    ),
                    InlineKeyboardButton(
                        text=_tr("💰 مبلغ تومان"),
                        callback_data=f"admin:plan:edit:price:{plan_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=_tr("💵 مبلغ USDT"),
                        callback_data=f"admin:plan:edit:usdt:{plan_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_tr("↕️ ترتیب نمایش"),
                        callback_data=f"admin:plan:edit:order:{plan_id}",
                    )
                ],
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=_tr("📊 سقف روزانه"),
                    callback_data=f"admin:plan:edit:daily:{plan_id}",
                ),
                InlineKeyboardButton(
                    text=_tr("📦 حداکثر حجم"),
                    callback_data=f"admin:plan:edit:size:{plan_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🎞 حداکثر کیفیت"),
                    callback_data=f"admin:plan:edit:quality:{plan_id}",
                ),
                InlineKeyboardButton(
                    text=_tr("⚙️ دانلود هم‌زمان"),
                    callback_data=f"admin:plan:edit:concurrency:{plan_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("🚀 تغییر اولویت"),
                    callback_data=f"admin:plan:toggle:priority:{plan_id}",
                ),
                InlineKeyboardButton(
                    text=_tr("📣 تغییر عضویت اجباری"),
                    callback_data=f"admin:plan:toggle:forced_join:{plan_id}",
                ),
            ],
        ]
    )

    if not plan.get("is_system"):
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=(
                            _tr("⛔️ غیرفعال‌سازی")
                            if plan.get("is_active")
                            else _tr("✅ فعال‌سازی")
                        ),
                        callback_data=f"admin:plan:toggle:active:{plan_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_tr("🗑 حذف نرم پلن"),
                        callback_data=f"admin:plan:delete:{plan_id}",
                    )
                ],
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=_tr("🔙 فهرست پلن‌ها"),
                callback_data="admin:plans",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_plan_quality_keyboard(*, mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=format_quality_limit(quality),
                    callback_data=f"admin:plan:choice:{mode}:quality:{quality}",
                )
                for quality in qualities
            ]
            for qualities in (
                (144, 240, 360),
                (480, 720, 1080),
                (1440, 2160),
            )
        ]
        + [
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
                    callback_data="admin:plan:cancel",
                )
            ]
        ]
    )


def build_plan_concurrency_keyboard(*, mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=str(value),
                    callback_data=(
                        f"admin:plan:choice:{mode}:concurrency:{value}"
                    ),
                )
                for value in (1, 2, 3)
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
                    callback_data="admin:plan:cancel",
                )
            ],
        ]
    )


def build_plan_boolean_keyboard(
    *,
    mode: str,
    field: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_tr("✅ بله"),
                    callback_data=f"admin:plan:choice:{mode}:{field}:yes",
                ),
                InlineKeyboardButton(
                    text=_tr("❌ خیر"),
                    callback_data=f"admin:plan:choice:{mode}:{field}:no",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
                    callback_data="admin:plan:cancel",
                )
            ],
        ]
    )


def build_plan_confirmation_keyboard(*, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_tr("✅ تأیید و ثبت"),
                    callback_data=f"admin:plan:{action}:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_tr("انصراف"),
                    callback_data="admin:plan:cancel",
                )
            ],
        ]
    )
