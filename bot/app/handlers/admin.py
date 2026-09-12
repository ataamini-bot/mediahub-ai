from app.localization import tr as _tr, localized_collection as _localized_collection
import html
import re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.admin_labels import (
    permission_label_fa,
    role_description_fa,
    role_label_fa,
)
from app.admin_runtime_settings import runtime_settings_text
from app.keyboards.admin import (
    build_admin_account_detail_keyboard,
    build_admin_accounts_keyboard,
    build_admin_home_keyboard,
    build_admin_plan_detail_keyboard,
    build_admin_plans_keyboard,
    build_admin_role_detail_keyboard,
    build_admin_roles_keyboard,
    build_change_confirmation_keyboard,
    build_final_danger_confirmation_keyboard,
    build_permission_picker_keyboard,
    build_plan_boolean_keyboard,
    build_plan_concurrency_keyboard,
    build_plan_confirmation_keyboard,
    build_plan_quality_keyboard,
    build_role_picker_keyboard,
)
from app.keyboards.payment import build_home_keyboard, format_toman, format_usdt
from app.keyboards.admin_settings import build_runtime_settings_keyboard
from app.services.backend import (
    BackendAPIError,
    create_admin_account,
    create_admin_plan,
    create_admin_role,
    get_admin_account,
    get_admin_context,
    get_admin_plan,
    list_admin_accounts,
    list_admin_permissions,
    list_admin_plans,
    list_admin_roles,
    list_application_settings,
    register_telegram_user,
    update_admin_account,
    update_admin_plan,
    update_admin_role,
)
from app.state.admin import AdminManagementStates
from app.utils.formatting import format_quality_limit


router = Router(name="admin")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


def _can(context: dict, permission: str) -> bool:
    return bool(
        context.get("is_superadmin")
        or permission in set(context.get("permissions", []))
    )


def _admin_panel_text(context: dict) -> str:
    role_names = [
        role_label_fa(str(code))
        for code in context.get("roles", [])
    ]

    if context.get("is_superadmin"):
        role_names.insert(0, _tr("سوپرادمین"))

    role_text = ", ".join(dict.fromkeys(role_names)) or _tr("مدیر")
    return (
        f"{_tr('⚙️ <b>پنل مدیریت MediaHub AI</b>\n\n👮 نقش: <code>')}{html.escape(role_text)}{_tr('</code>\n🔐 تعداد دسترسی\u200cها: <code>')}{len(context.get('permissions', []))}</code>"
    )


async def _context_or_none(telegram_id: int) -> dict | None:
    try:
        context = await get_admin_context(telegram_id)
    except BackendAPIError:
        return None

    return context if context.get("is_admin") else None


def _backend_error_text(exc: BackendAPIError) -> str:
    detail = exc.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    messages = {
        "admin_target_not_found": (
            _tr("کاربر پیدا نشد. کاربر باید ابتدا ربات را Start کرده باشد.")
        ),
        "admin_account_not_found": _tr("حساب مدیر پیدا نشد."),
        "admin_account_conflict": _tr("این کاربر هم‌اکنون مدیر فعال است."),
        "last_superadmin": (
            _tr("آخرین سوپرادمین فعال را نمی‌توان غیرفعال یا تنزل داد.")
        ),
        "admin_role_not_found": _tr("یک یا چند نقش انتخاب‌شده معتبر نیست."),
        "admin_role_conflict": _tr("کد این نقش قبلاً ثبت شده است."),
        "admin_role_validation": _tr("ترکیب نقش یا دسترسی معتبر نیست."),
        "admin_role_in_use": (
            _tr("این تغییر بعضی مدیران را بدون دسترسی پنل می‌گذارد؛ "
            "ابتدا نقش آن مدیران را عوض کنید.")
        ),
        "system_role_protected": _tr("نقش سیستمی قابل غیرفعال‌سازی نیست."),
        "plan_not_found": _tr("پلن پیدا نشد."),
        "plan_conflict": _tr("پلنی با این نام از قبل وجود دارد."),
        "plan_validation": _tr("اطلاعات پلن معتبر نیست."),
        "system_plan_protected": (
            _tr("نام، قیمت، مدت و وضعیت پلن رایگان قابل تغییر نیست؛ "
            "محدودیت‌های آن قابل ویرایش است.")
        ),
    }

    if code in messages:
        return messages[code]

    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])[:180]

    return str(detail)[:180]


def _account_identity(account: dict) -> str:
    full_name = " ".join(
        str(value)
        for value in [account.get("first_name"), account.get("last_name")]
        if value
    ).strip()
    username = account.get("username")
    parts = [full_name or _tr("بدون نام")]

    if username:
        parts.append(f"@{username}")

    return " — ".join(parts)


def _admin_account_text(account: dict) -> str:
    roles = account.get("roles", [])
    role_text = ", ".join(
        role_label_fa(
            str(role.get("code") or ""),
            str(role.get("name") or role.get("code") or ""),
        )
        for role in roles
    )[:1200] or _tr("بدون نقش")
    status = _tr("فعال ✅") if account.get("is_active") else _tr("غیرفعال ⛔️")
    authority = (
        _tr("سوپرادمین 👑") if account.get("is_superadmin") else _tr("مدیر 👮")
    )
    return (
        f"{_tr('👤 <b>مشخصات مدیر</b>\n\nنام: ')}{html.escape(_account_identity(account))}\nTelegram ID: <code>{int(account['telegram_id'])}{_tr('</code>\nسطح: <b>')}{authority}{_tr('</b>\nوضعیت: <b>')}{status}{_tr('</b>\nنقش\u200cها: <code>')}{html.escape(role_text)}</code>"
    )


def _admin_role_text(role: dict) -> str:
    permissions = role.get("permission_codes", [])
    permission_lines = "\n".join(
        f"• {html.escape(permission_label_fa(str(code)))}"
        for code in permissions[:50]
    ) or _tr("• بدون دسترسی")
    status = _tr("فعال ✅") if role.get("is_active") else _tr("غیرفعال ⛔️")
    kind = _tr("سیستمی 🔒") if role.get("is_system") else _tr("سفارشی 🧩")
    role_code = str(role.get("code") or "")
    role_name = role_label_fa(role_code, str(role.get("name") or role_code))
    description = role_description_fa(
        role_code,
        str(role.get("description") or "—"),
    )
    return (
        f"{_tr('🔐 <b>مشخصات نقش</b>\n\nنام: <b>')}{html.escape(role_name)}{_tr('</b>\nشناسه فنی: <code>')}{html.escape(role_code)}{_tr('</code>\nنوع: ')}{kind}{_tr('\nوضعیت: ')}{status}{_tr('\nمدیران دارای نقش: <code>')}{int(role.get('assignment_count', 0))}{_tr('</code>\nتوضیح: ')}{html.escape(description)}{_tr('\n\n<b>دسترسی\u200cها:</b>\n')}{permission_lines}"
    )


_PLAN_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def _parse_plan_integer(value: str) -> int | None:
    normalized = (
        str(value or "")
        .strip()
        .translate(_PLAN_DIGIT_TRANSLATION)
        .replace(",", "")
        .replace("٬", "")
    )

    if not normalized.isdigit():
        return None

    return int(normalized)


def _parse_plan_decimal(value: str) -> Decimal | None:
    normalized = (
        str(value or "")
        .strip()
        .translate(_PLAN_DIGIT_TRANSLATION)
        .replace("٫", ".")
        .replace("٬", "")
        .replace(",", ".")
    )
    if not re.fullmatch(r"\d+(?:\.\d{1,4})?", normalized):
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _plan_daily_limit_text(value: object) -> str:
    if value is None:
        return _tr("نامحدود")

    return f"{int(value):,}{_tr(' خروجی')}"


def _admin_plan_text(plan: dict) -> str:
    is_free = bool(plan.get("is_system"))
    status = _tr("فعال ✅") if plan.get("is_active") else _tr("غیرفعال ⛔️")
    plan_type = _tr("رایگان و سیستمی 🆓") if is_free else _tr("سفارشی 💎")
    duration = _tr("همیشگی") if is_free else f"{int(plan['duration_days'])}{_tr(' روز')}"
    price = _tr("رایگان") if is_free else format_toman(plan.get("price", 0))
    price_usdt = (
        "—"
        if plan.get("price_usdt") is None
        else format_usdt(plan.get("price_usdt"))
    )
    file_size = (
        f"{int(plan['max_file_size_mb'])} MB"
        if plan.get("max_file_size_mb") is not None
        else _tr("نامحدود")
    )
    quality = (
        format_quality_limit(int(plan["max_quality"]))
        if plan.get("max_quality") is not None
        else _tr("نامحدود")
    )
    priority = _tr("بالا") if plan.get("priority_processing") else _tr("عادی")
    forced_join = _tr("بله") if plan.get("forced_join_required") else _tr("خیر")
    description = html.escape(str(plan.get("description") or "—"))
    description_en = html.escape(str(plan.get("description_en") or "—"))
    return (
        f"{_tr('📦 <b>مشخصات پلن</b>\n\nنام فارسی: <b>')}{html.escape(str(plan['name']))}{_tr('</b>\nنام انگلیسی: <b>')}{html.escape(str(plan.get('name_en') or plan['name']))}{_tr('</b>\nنوع: ')}{plan_type}{_tr('\nوضعیت: <b>')}{status}{_tr('</b>\nمدت: <code>')}{duration}{_tr('</code>\nمبلغ ریالی: <b>')}{price}{_tr('</b>\nمبلغ بین\u200cالمللی: <b>')}{price_usdt}{_tr('</b>\n\n📊 سقف روزانه: <code>')}{_plan_daily_limit_text(plan.get('daily_download_limit'))}{_tr('</code>\n📦 حداکثر حجم: <code>')}{file_size}{_tr('</code>\n🎞 حداکثر کیفیت: <code>')}{quality}{_tr('</code>\n⚙️ دانلود هم\u200cزمان: <code>')}{int(plan.get('max_concurrent_downloads', 1))}{_tr('</code>\n🚀 اولویت پردازش: <code>')}{priority}{_tr('</code>\n📣 عضویت اجباری: <code>')}{forced_join}{_tr('</code>\n↕️ ترتیب نمایش: <code>')}{int(plan.get('sort_order', 0))}{_tr('</code>\n\n📝 توضیح فارسی: ')}{description}{_tr('\n🌐 توضیح انگلیسی: ')}{description_en}"
    )


def _plan_create_summary(data: dict) -> str:
    daily_limit = data.get("daily_download_limit")
    description = html.escape(str(data.get("description") or "—"))
    description_en = html.escape(str(data.get("description_en") or "—"))
    return (
        f"{_tr('➕ <b>مرور پلن جدید</b>\n\nنام فارسی: <b>')}{html.escape(str(data['name']))}{_tr('</b>\nنام انگلیسی: <b>')}{html.escape(str(data['name_en']))}{_tr('</b>\nمدت: <code>')}{int(data['duration_days'])}{_tr(' روز</code>\nمبلغ ریالی: <b>')}{format_toman(data['price'])}{_tr('</b>\nمبلغ بین\u200cالمللی: <b>')}{(format_usdt(data['price_usdt']) if data.get('price_usdt') is not None else _tr('غیرفعال'))}{_tr('</b>\nسقف روزانه: <code>')}{_plan_daily_limit_text(daily_limit)}{_tr('</code>\nحداکثر حجم: <code>')}{int(data['max_file_size_mb'])}{_tr(' MB</code>\nحداکثر کیفیت: <code>')}{format_quality_limit(int(data['max_quality']))}{_tr('</code>\nدانلود هم\u200cزمان: <code>')}{int(data['max_concurrent_downloads'])}{_tr('</code>\nاولویت پردازش: <code>')}{(_tr('بالا') if data['priority_processing'] else _tr('عادی'))}{_tr('</code>\nعضویت اجباری: <code>')}{(_tr('بله') if data['forced_join_required'] else _tr('خیر'))}{_tr('</code>\nتوضیح فارسی: ')}{description}{_tr('\nتوضیح انگلیسی: ')}{description_en}"
    )


def _plan_update_summary(plan: dict, changes: dict) -> str:
    labels = {
        "name": _tr("نام فارسی"),
        "name_en": _tr("نام انگلیسی"),
        "description": _tr("توضیح"),
        "description_en": _tr("توضیح انگلیسی"),
        "duration_days": _tr("مدت به روز"),
        "price": _tr("مبلغ تومان"),
        "price_usdt": _tr("مبلغ USDT"),
        "daily_download_limit": _tr("سقف روزانه"),
        "max_file_size_mb": _tr("حداکثر حجم MB"),
        "max_quality": _tr("حداکثر کیفیت"),
        "max_concurrent_downloads": _tr("دانلود هم‌زمان"),
        "priority_processing": _tr("اولویت پردازش"),
        "forced_join_required": _tr("عضویت اجباری"),
        "sort_order": _tr("ترتیب نمایش"),
        "is_active": _tr("وضعیت فعال"),
        "is_deleted": _tr("حذف نرم"),
    }
    lines = [
        _tr("✏️ <b>مرور تغییر پلن</b>"),
        "",
        f"{_tr('پلن: <b>')}{html.escape(str(plan['name']))}</b>",
    ]

    for key, value in changes.items():
        if isinstance(value, bool):
            rendered = _tr("بله") if value else _tr("خیر")
        elif value is None:
            rendered = _tr("نامحدود / خالی")
        elif key == "price":
            rendered = format_toman(value)
        elif key == "price_usdt":
            rendered = _tr("غیرفعال") if value is None else format_usdt(value)
        else:
            rendered = str(value)

        lines.append(
            f"{labels.get(key, key)}: <code>{html.escape(rendered)}</code>"
        )

    return "\n".join(lines)


async def _show_plans(message: Message, actor_telegram_id: int) -> None:
    plans = await list_admin_plans(actor_telegram_id)
    custom_count = sum(not bool(plan.get("is_system")) for plan in plans)
    await message.edit_text(
        (
            f"{_tr('📦 <b>مدیریت پلن\u200cها</b>\n\nپلن رایگان همیشه وجود دارد و محدودیت\u200cهایش قابل ویرایش است.\nتعداد پلن\u200cهای سفارشی: <code>')}{custom_count}</code>"
        ),
        parse_mode="HTML",
        reply_markup=build_admin_plans_keyboard(plans),
    )


async def _show_plan_detail(
    message: Message,
    *,
    actor_telegram_id: int,
    plan_id: int,
) -> None:
    plan = await get_admin_plan(
        actor_telegram_id=actor_telegram_id,
        plan_id=plan_id,
    )
    await message.edit_text(
        _admin_plan_text(plan),
        parse_mode="HTML",
        reply_markup=build_admin_plan_detail_keyboard(plan),
    )


async def _show_accounts(
    message: Message,
    actor_telegram_id: int,
    *,
    context: dict | None = None,
) -> None:
    if context is None:
        context = await _context_or_none(actor_telegram_id)
    if context is None:
        return
    can_manage_accounts = _can(context, "admins.manage")
    can_manage_roles = _can(context, "roles.manage")
    if not (can_manage_accounts or can_manage_roles):
        return
    accounts = []
    text = _tr("👮 <b>مدیریت مدیران</b>\n\n")
    if can_manage_accounts:
        accounts = await list_admin_accounts(actor_telegram_id)
        active_count = sum(bool(row.get("is_active")) for row in accounts)
        text += (
            f"{_tr('تعداد کل: <code>')}{len(accounts)}{_tr('</code>\nفعال: <code>')}{active_count}{_tr('</code>\n\nبرای مشاهده یا ویرایش، یک مدیر را انتخاب کنید.')}"
        )
    if can_manage_roles:
        text += _tr("\n\nبرای تنظیم نقش‌ها، «نقش‌ها و دسترسی‌ها» را انتخاب کنید.")
    await message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=build_admin_accounts_keyboard(
            accounts,
            can_manage_accounts=can_manage_accounts,
            can_manage_roles=can_manage_roles,
        ),
    )


async def _show_account_detail(
    message: Message,
    *,
    actor_telegram_id: int,
    target_telegram_id: int,
) -> None:
    account = await get_admin_account(
        actor_telegram_id=actor_telegram_id,
        target_telegram_id=target_telegram_id,
    )
    await message.edit_text(
        _admin_account_text(account),
        parse_mode="HTML",
        reply_markup=build_admin_account_detail_keyboard(account),
    )


async def _show_roles(message: Message, actor_telegram_id: int) -> None:
    roles = await list_admin_roles(actor_telegram_id)
    await message.edit_text(
        (
            _tr("🔐 <b>نقش‌ها و سطح دسترسی</b>\n\n"
            "🔒 نقش سیستمی، 🧩 نقش سفارشی\n"
            "برای مشاهده یا ویرایش، یک نقش را انتخاب کنید.")
        ),
        parse_mode="HTML",
        reply_markup=build_admin_roles_keyboard(roles),
    )


async def _show_role_detail(
    message: Message,
    *,
    actor_telegram_id: int,
    role_id: int,
) -> None:
    roles = await list_admin_roles(actor_telegram_id)
    role = next((row for row in roles if int(row["id"]) == role_id), None)

    if role is None:
        raise BackendAPIError(status_code=404, detail="Role not found")

    await message.edit_text(
        _admin_role_text(role),
        parse_mode="HTML",
        reply_markup=build_admin_role_detail_keyboard(role),
    )


def _selected_names(rows: list[dict], selected_codes: set[str]) -> str:
    return ", ".join(
        role_label_fa(
            str(row.get("code") or ""),
            str(row.get("name") or row.get("code") or ""),
        )
        for row in rows
        if str(row.get("code")) in selected_codes
    ) or _tr("بدون نقش")


async def _edit_role_picker(message: Message, data: dict) -> None:
    roles = list(data.get("available_roles", []))
    selected_codes = set(data.get("selected_role_codes", []))
    await message.edit_text(
        (
            _tr("🔐 <b>انتخاب نقش‌ها</b>\n\n"
            "هر مدیر می‌تواند هم‌زمان چند نقش داشته باشد. "
            "روی نقش‌ها بزنید و سپس ادامه را انتخاب کنید.")
        ),
        parse_mode="HTML",
        reply_markup=build_role_picker_keyboard(
            roles,
            selected_codes,
            allow_superadmin=data.get("workflow") == "admin_create",
            is_superadmin=bool(data.get("is_superadmin")),
        ),
    )


async def _edit_permission_picker(message: Message, data: dict) -> None:
    permissions = list(data.get("available_permissions", []))
    selected_codes = set(data.get("selected_permission_codes", []))
    await message.edit_text(
        (
            _tr("🔐 <b>انتخاب دسترسی‌ها</b>\n\n"
            "دسترسی‌های موردنیاز را انتخاب کنید و سپس ادامه را بزنید.")
        ),
        parse_mode="HTML",
        reply_markup=build_permission_picker_keyboard(
            permissions,
            selected_codes,
        ),
    )


async def show_admin_panel_message(
    message: Message,
    state: FSMContext,
    *,
    register_user: bool = True,
) -> bool:
    """Open the admin panel for an authorized private-chat user."""
    if message.from_user is None:
        return False

    if register_user:
        try:
            await register_telegram_user(message)
        except Exception:
            return False

    context = await _context_or_none(message.from_user.id)

    if context is None:
        return False

    await state.clear()
    permissions = set(context.get("permissions", []))
    await message.answer(
        _admin_panel_text(context),
        parse_mode="HTML",
        reply_markup=build_admin_home_keyboard(
            permissions,
            is_superadmin=bool(context.get("is_superadmin")),
        ),
    )
    return True


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    await show_admin_panel_message(message, state)


@router.callback_query(F.data == "admin:open")
async def open_admin_panel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None:
        await callback.answer(_tr("دسترسی مدیریت ندارید."), show_alert=True)
        return

    await state.clear()
    permissions = set(context.get("permissions", []))
    await callback.message.edit_text(
        _admin_panel_text(context),
        parse_mode="HTML",
        reply_markup=build_admin_home_keyboard(
            permissions,
            is_superadmin=bool(context.get("is_superadmin")),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:settings")
async def show_application_settings(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "settings.view"):
        await callback.answer(_tr("دسترسی مشاهده تنظیمات ندارید."), show_alert=True)
        return

    try:
        await state.clear()
        settings_rows = await list_application_settings(
            callback.from_user.id
        )
    except BackendAPIError:
        await callback.answer(_tr("دریافت تنظیمات ممکن نشد."), show_alert=True)
        return

    await callback.message.edit_text(
        runtime_settings_text(settings_rows),
        parse_mode="HTML",
        reply_markup=build_runtime_settings_keyboard(
            settings_rows,
            can_manage=_can(context, "settings.manage"),
            can_manage_channels=_can(context, "forced_join.manage"),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:accounts")
async def show_admin_accounts(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not (
        _can(context, "admins.manage") or _can(context, "roles.manage")
    ):
        await callback.answer(_tr("دسترسی مدیریت مدیران ندارید."), show_alert=True)
        return

    try:
        await state.clear()
        await _show_accounts(
            callback.message, callback.from_user.id, context=context,
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:account:add")
async def start_add_admin(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "admins.manage"):
        await callback.answer(_tr("دسترسی مدیریت مدیران ندارید."), show_alert=True)
        return

    try:
        roles = await list_admin_roles(
            callback.from_user.id,
            include_inactive=False,
        )
        await state.clear()
        await state.update_data(
            workflow="admin_create",
            available_roles=roles,
            selected_role_codes=[],
            is_superadmin=False,
            dangerous=False,
        )
        await state.set_state(AdminManagementStates.waiting_for_admin_id)
        await callback.message.edit_text(
            (
                _tr("➕ <b>افزودن مدیر</b>\n\n"
                "Telegram ID عددی کاربر را بفرستید.\n"
                "کاربر باید قبلاً ربات را Start کرده باشد.")
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("Telegram ID را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.message(StateFilter(AdminManagementStates.waiting_for_admin_id))
async def receive_admin_id(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    raw_value = str(message.text or "").strip()

    try:
        target_telegram_id = int(raw_value)
    except ValueError:
        await message.answer(
            _tr("❌ Telegram ID باید فقط عدد باشد. دوباره ارسال کنید."),
            reply_markup=ForceReply(selective=True),
        )
        return

    if target_telegram_id <= 0:
        await message.answer(
            _tr("❌ Telegram ID معتبر نیست. دوباره ارسال کنید."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(target_telegram_id=target_telegram_id)
    await state.set_state(AdminManagementStates.selecting_admin_roles)
    data = await state.get_data()
    roles = list(data.get("available_roles", []))
    await message.answer(
        (
            _tr("🔐 <b>انتخاب نقش‌ها</b>\n\n"
            "هر مدیر می‌تواند هم‌زمان چند نقش داشته باشد. "
            "روی نقش‌ها بزنید و سپس ادامه را انتخاب کنید.")
        ),
        parse_mode="HTML",
        reply_markup=build_role_picker_keyboard(
            roles,
            set(),
            allow_superadmin=True,
            is_superadmin=False,
        ),
    )


@router.callback_query(
    StateFilter(AdminManagementStates.selecting_admin_roles),
    F.data.startswith("admin:rolepick:"),
)
async def toggle_admin_role(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    choice = callback.data.rsplit(":", 1)[-1]
    data = await state.get_data()

    if choice == "done":
        selected = set(data.get("selected_role_codes", []))
        is_superadmin = bool(data.get("is_superadmin"))

        if not selected and not is_superadmin:
            await callback.answer(
                _tr("برای مدیر معمولی حداقل یک نقش انتخاب کنید."),
                show_alert=True,
            )
            return

        await state.set_state(AdminManagementStates.waiting_for_admin_reason)
        await callback.message.edit_text(
            (
                _tr("📝 <b>دلیل تغییر</b>\n\n"
                "دلیل افزودن یا تغییر دسترسی این مدیر را بنویسید. "
                "این متن در Audit Log ثبت می‌شود.")
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("دلیل را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
        return

    if choice == "super":
        if data.get("workflow") != "admin_create":
            await callback.answer(_tr("این گزینه در این فرم فعال نیست."))
            return

        await state.update_data(
            is_superadmin=not bool(data.get("is_superadmin")),
            dangerous=True,
        )
        await _edit_role_picker(callback.message, await state.get_data())
        await callback.answer()
        return

    try:
        role_id = int(choice)
    except ValueError:
        await callback.answer(_tr("انتخاب نامعتبر است."), show_alert=True)
        return

    roles = list(data.get("available_roles", []))
    role = next((row for row in roles if int(row["id"]) == role_id), None)

    if role is None:
        await callback.answer(_tr("نقش پیدا نشد."), show_alert=True)
        return

    selected = set(data.get("selected_role_codes", []))
    code = str(role["code"])

    if code in selected:
        selected.remove(code)
    else:
        selected.add(code)

    await state.update_data(selected_role_codes=sorted(selected))
    await _edit_role_picker(callback.message, await state.get_data())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:account:-?\d+$"))
async def show_admin_account_detail(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "admins.manage"):
        await callback.answer(_tr("دسترسی مدیریت مدیران ندارید."), show_alert=True)
        return

    try:
        target_telegram_id = int(callback.data.rsplit(":", 1)[-1])
        await _show_account_detail(
            callback.message,
            actor_telegram_id=callback.from_user.id,
            target_telegram_id=target_telegram_id,
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data.startswith("admin:account:roles:"))
async def start_edit_admin_roles(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "admins.manage"):
        await callback.answer(_tr("دسترسی مدیریت مدیران ندارید."), show_alert=True)
        return

    target_telegram_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        account = await get_admin_account(
            actor_telegram_id=callback.from_user.id,
            target_telegram_id=target_telegram_id,
        )
        roles = await list_admin_roles(
            callback.from_user.id,
            include_inactive=False,
        )
        await state.clear()
        await state.update_data(
            workflow="admin_roles_update",
            target_telegram_id=target_telegram_id,
            available_roles=roles,
            selected_role_codes=[
                role["code"]
                for role in account.get("roles", [])
                if role.get("is_active")
            ],
            is_superadmin=bool(account.get("is_superadmin")),
            dangerous=False,
        )
        await state.set_state(AdminManagementStates.selecting_admin_roles)
        await _edit_role_picker(callback.message, await state.get_data())
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data.startswith("admin:account:super:"))
async def start_change_superadmin(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "admins.manage"):
        await callback.answer(_tr("دسترسی مدیریت مدیران ندارید."), show_alert=True)
        return

    target_telegram_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        account = await get_admin_account(
            actor_telegram_id=callback.from_user.id,
            target_telegram_id=target_telegram_id,
        )
        await state.clear()
        await state.update_data(
            workflow="admin_super_update",
            target_telegram_id=target_telegram_id,
            is_superadmin=not bool(account.get("is_superadmin")),
            dangerous=True,
        )
        await state.set_state(AdminManagementStates.waiting_for_admin_reason)
        await callback.message.edit_text(
            (
                _tr("⚠️ <b>تغییر سطح سوپرادمین</b>\n\n"
                "دلیل این تغییر حساس را بنویسید.")
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("دلیل را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data.startswith("admin:account:status:"))
async def start_change_admin_status(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "admins.manage"):
        await callback.answer(_tr("دسترسی مدیریت مدیران ندارید."), show_alert=True)
        return

    target_telegram_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        account = await get_admin_account(
            actor_telegram_id=callback.from_user.id,
            target_telegram_id=target_telegram_id,
        )
        next_active = not bool(account.get("is_active"))
        await state.clear()
        await state.update_data(
            workflow="admin_status_update",
            target_telegram_id=target_telegram_id,
            is_active=next_active,
            dangerous=not next_active,
        )
        await state.set_state(AdminManagementStates.waiting_for_admin_reason)
        await callback.message.edit_text(
            (
                _tr("📝 <b>تغییر وضعیت مدیر</b>\n\n"
                "دلیل فعال‌سازی یا غیرفعال‌سازی را بنویسید.")
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("دلیل را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.message(StateFilter(AdminManagementStates.waiting_for_admin_reason))
async def receive_admin_change_reason(
    message: Message,
    state: FSMContext,
) -> None:
    reason = str(message.text or "").strip()

    if len(reason) < 3:
        await message.answer(
            _tr("❌ دلیل باید حداقل ۳ کاراکتر باشد. دوباره بفرستید."),
            reply_markup=ForceReply(selective=True),
        )
        return

    if len(reason) > 500:
        await message.answer(
            _tr("❌ دلیل حداکثر ۵۰۰ کاراکتر است. خلاصه‌تر بفرستید."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(reason=reason)
    data = await state.get_data()
    workflow = data.get("workflow")
    selected = set(data.get("selected_role_codes", []))
    roles = list(data.get("available_roles", []))

    if workflow == "admin_create":
        summary = (
            f"{_tr('➕ افزودن مدیر جدید\nTelegram ID: <code>')}{int(data['target_telegram_id'])}{_tr('</code>\nنقش\u200cها: <code>')}{html.escape(_selected_names(roles, selected))}{_tr('</code>\nسوپرادمین: <b>')}{(_tr('بله') if data.get('is_superadmin') else _tr('خیر'))}</b>"
        )
    elif workflow == "admin_roles_update":
        summary = (
            f"{_tr('🔐 ویرایش نقش\u200cهای مدیر\nTelegram ID: <code>')}{int(data['target_telegram_id'])}{_tr('</code>\nنقش\u200cهای جدید: <code>')}{html.escape(_selected_names(roles, selected))}</code>"
        )
    elif workflow == "admin_super_update":
        summary = (
            f"{_tr('👑 تغییر سطح سوپرادمین\nTelegram ID: <code>')}{int(data['target_telegram_id'])}{_tr('</code>\nمقدار جدید: <b>')}{(_tr('بله') if data.get('is_superadmin') else _tr('خیر'))}</b>"
        )
    else:
        summary = (
            f"{_tr('🚦 تغییر وضعیت مدیر\nTelegram ID: <code>')}{int(data['target_telegram_id'])}{_tr('</code>\nوضعیت جدید: <b>')}{(_tr('فعال') if data.get('is_active') else _tr('غیرفعال'))}</b>"
        )

    await state.set_state(AdminManagementStates.confirming_admin_change)
    await message.answer(
        (
            f"{_tr('<b>مرور تغییر</b>\n\n')}{summary}{_tr('\n\nدلیل: ')}{html.escape(reason)}"
        ),
        parse_mode="HTML",
        reply_markup=build_change_confirmation_keyboard(
            dangerous=bool(data.get("dangerous")),
        ),
    )


async def _apply_admin_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    data = await state.get_data()
    workflow = data.get("workflow")
    target_telegram_id = int(data["target_telegram_id"])

    try:
        if workflow == "admin_create":
            account = await create_admin_account(
                actor_telegram_id=callback.from_user.id,
                target_telegram_id=target_telegram_id,
                role_codes=list(data.get("selected_role_codes", [])),
                is_superadmin=bool(data.get("is_superadmin")),
                reason=str(data["reason"]),
            )
        else:
            changes: dict = {}

            if workflow == "admin_roles_update":
                changes["role_codes"] = list(
                    data.get("selected_role_codes", [])
                )
            elif workflow == "admin_super_update":
                changes["is_superadmin"] = bool(data.get("is_superadmin"))
            elif workflow == "admin_status_update":
                changes["is_active"] = bool(data.get("is_active"))

            account = await update_admin_account(
                actor_telegram_id=callback.from_user.id,
                target_telegram_id=target_telegram_id,
                reason=str(data["reason"]),
                **changes,
            )

        await state.clear()
        await callback.message.edit_text(
            _tr("✅ تغییر با موفقیت ثبت شد.\n\n") + _admin_account_text(account),
            parse_mode="HTML",
            reply_markup=build_admin_account_detail_keyboard(account),
        )
        await callback.answer(_tr("ثبت شد"))
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(
    StateFilter(AdminManagementStates.confirming_admin_change),
    F.data == "admin:change:confirm",
)
async def confirm_admin_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()

    if data.get("dangerous"):
        if isinstance(callback.message, Message):
            await state.set_state(
                AdminManagementStates.confirming_dangerous_admin_change
            )
            await callback.message.edit_text(
                (
                    _tr("🚨 <b>تأیید نهایی تغییر حساس</b>\n\n"
                    "این عملیات می‌تواند دسترسی مدیریتی را تغییر دهد. "
                    "فقط در صورت اطمینان کامل تأیید کنید.")
                ),
                parse_mode="HTML",
                reply_markup=build_final_danger_confirmation_keyboard(),
            )
            await callback.answer()
        return

    await _apply_admin_change(callback, state)


@router.callback_query(
    StateFilter(AdminManagementStates.confirming_dangerous_admin_change),
    F.data == "admin:change:final",
)
async def final_confirm_admin_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await _apply_admin_change(callback, state)


@router.callback_query(F.data == "admin:roles")
async def show_admin_roles(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "roles.manage"):
        await callback.answer(_tr("دسترسی مدیریت نقش‌ها ندارید."), show_alert=True)
        return

    try:
        await state.clear()
        await _show_roles(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:role:add")
async def start_add_admin_role(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "roles.manage"):
        await callback.answer(_tr("دسترسی مدیریت نقش‌ها ندارید."), show_alert=True)
        return

    await state.clear()
    await state.update_data(workflow="role_create")
    await state.set_state(AdminManagementStates.waiting_for_role_code)
    await callback.message.edit_text(
        (
            _tr("➕ <b>ساخت نقش سفارشی</b>\n\n"
            "یک کد انگلیسی یکتا بنویسید؛ مانند:\n"
            "<code>content_reviewer</code>")
        ),
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.message.answer(
        _tr("کد نقش را ارسال کنید:"),
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.message(StateFilter(AdminManagementStates.waiting_for_role_code))
async def receive_role_code(message: Message, state: FSMContext) -> None:
    code = str(message.text or "").strip().lower()

    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,99}", code):
        await message.answer(
            (
                _tr("❌ کد نامعتبر است. با حرف انگلیسی شروع شود و فقط از "
                "حروف کوچک، عدد، نقطه، خط تیره یا زیرخط استفاده کند.")
            ),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(role_code=code)
    await state.set_state(AdminManagementStates.waiting_for_role_name)
    await message.answer(
        _tr("نام نمایشی نقش را بنویسید؛ مثلاً «بازبین محتوا»: "),
        reply_markup=ForceReply(selective=True),
    )


@router.callback_query(F.data.regexp(r"^admin:role:\d+$"))
async def show_admin_role_detail(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "roles.manage"):
        await callback.answer(_tr("دسترسی مدیریت نقش‌ها ندارید."), show_alert=True)
        return

    try:
        role_id = int(callback.data.rsplit(":", 1)[-1])
        await _show_role_detail(
            callback.message,
            actor_telegram_id=callback.from_user.id,
            role_id=role_id,
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


async def _load_role_for_edit(
    actor_telegram_id: int,
    role_id: int,
) -> dict:
    roles = await list_admin_roles(actor_telegram_id)
    role = next((row for row in roles if int(row["id"]) == role_id), None)

    if role is None:
        raise BackendAPIError(status_code=404, detail="Role not found")

    return role


@router.callback_query(F.data.startswith("admin:role:name:"))
async def start_rename_admin_role(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    role_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        role = await _load_role_for_edit(callback.from_user.id, role_id)
        await state.clear()
        await state.update_data(
            workflow="role_name_update",
            role=role,
            role_id=role_id,
        )
        await state.set_state(AdminManagementStates.waiting_for_role_name)
        await callback.message.edit_text(
            (
                f"{_tr('✏️ <b>تغییر نام نقش</b>\n\nنام فعلی: <b>')}{html.escape(str(role['name']))}</b>"
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("نام جدید را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.message(StateFilter(AdminManagementStates.waiting_for_role_name))
async def receive_role_name(message: Message, state: FSMContext) -> None:
    name = str(message.text or "").strip()

    if not 2 <= len(name) <= 150:
        await message.answer(
            _tr("❌ نام نقش باید بین ۲ تا ۱۵۰ کاراکتر باشد."),
            reply_markup=ForceReply(selective=True),
        )
        return

    data = await state.get_data()
    await state.update_data(role_name=name)

    if data.get("workflow") == "role_create":
        await state.set_state(AdminManagementStates.waiting_for_role_description)
        await message.answer(
            (
                _tr("توضیح نقش را ارسال کنید. اگر توضیح نمی‌خواهید، "
                "فقط یک خط تیره <code>-</code> بفرستید.")
            ),
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.set_state(AdminManagementStates.waiting_for_role_reason)
    await message.answer(
        _tr("دلیل تغییر نام را ارسال کنید:"),
        reply_markup=ForceReply(selective=True),
    )


@router.callback_query(F.data.startswith("admin:role:description:"))
async def start_edit_role_description(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    role_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        role = await _load_role_for_edit(callback.from_user.id, role_id)
        await state.clear()
        await state.update_data(
            workflow="role_description_update",
            role=role,
            role_id=role_id,
        )
        await state.set_state(
            AdminManagementStates.waiting_for_role_description
        )
        await callback.message.edit_text(
            (
                _tr("📝 <b>تغییر توضیح نقش</b>\n\n"
                "توضیح جدید را بفرستید؛ برای حذف توضیح، فقط "
                "یک خط تیره <code>-</code> ارسال کنید.")
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("توضیح جدید را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.message(
    StateFilter(AdminManagementStates.waiting_for_role_description)
)
async def receive_role_description(
    message: Message,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return

    raw_description = str(message.text or "").strip()

    if len(raw_description) > 2000:
        await message.answer(
            _tr("❌ توضیح نقش حداکثر ۲۰۰۰ کاراکتر است."),
            reply_markup=ForceReply(selective=True),
        )
        return

    description = None if raw_description == "-" else raw_description or None
    await state.update_data(
        role_description=description,
        description_supplied=True,
    )
    data = await state.get_data()

    if data.get("workflow") == "role_create":
        try:
            permissions = await list_admin_permissions(message.from_user.id)
        except BackendAPIError as exc:
            await message.answer("❌ " + _backend_error_text(exc))
            return

        await state.update_data(
            available_permissions=permissions,
            selected_permission_codes=[],
        )
        await state.set_state(
            AdminManagementStates.selecting_role_permissions
        )
        await message.answer(
            (
                _tr("🔐 <b>انتخاب دسترسی‌ها</b>\n\n"
                "دسترسی‌های موردنیاز را انتخاب کنید و سپس ادامه را بزنید.")
            ),
            parse_mode="HTML",
            reply_markup=build_permission_picker_keyboard(
                permissions,
                set(),
            ),
        )
        return

    await state.set_state(AdminManagementStates.waiting_for_role_reason)
    await message.answer(
        _tr("دلیل تغییر توضیح را ارسال کنید:"),
        reply_markup=ForceReply(selective=True),
    )


@router.callback_query(F.data.startswith("admin:role:permissions:"))
async def start_edit_role_permissions(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    role_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        role = await _load_role_for_edit(callback.from_user.id, role_id)
        permissions = await list_admin_permissions(callback.from_user.id)
        await state.clear()
        await state.update_data(
            workflow="role_permissions_update",
            role=role,
            role_id=role_id,
            available_permissions=permissions,
            selected_permission_codes=list(role.get("permission_codes", [])),
        )
        await state.set_state(
            AdminManagementStates.selecting_role_permissions
        )
        await _edit_permission_picker(callback.message, await state.get_data())
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(
    StateFilter(AdminManagementStates.selecting_role_permissions),
    F.data.startswith("admin:permpick:"),
)
async def toggle_role_permission(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    choice = callback.data.rsplit(":", 1)[-1]
    data = await state.get_data()

    if choice == "done":
        selected = set(data.get("selected_permission_codes", []))

        if not selected:
            await callback.answer(
                _tr("حداقل یک دسترسی انتخاب کنید."),
                show_alert=True,
            )
            return

        await state.set_state(AdminManagementStates.waiting_for_role_reason)
        await callback.message.edit_text(
            (
                _tr("📝 <b>دلیل تغییر</b>\n\n"
                "دلیل ساخت یا تغییر این نقش را بنویسید. "
                "این متن در Audit Log ثبت می‌شود.")
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("دلیل را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
        return

    try:
        permission_id = int(choice)
    except ValueError:
        await callback.answer(_tr("انتخاب نامعتبر است."), show_alert=True)
        return

    permissions = list(data.get("available_permissions", []))
    permission = next(
        (row for row in permissions if int(row["id"]) == permission_id),
        None,
    )

    if permission is None:
        await callback.answer(_tr("دسترسی پیدا نشد."), show_alert=True)
        return

    selected = set(data.get("selected_permission_codes", []))
    code = str(permission["code"])

    if code in selected:
        selected.remove(code)
    else:
        selected.add(code)

    await state.update_data(selected_permission_codes=sorted(selected))
    await _edit_permission_picker(callback.message, await state.get_data())
    await callback.answer()


@router.callback_query(F.data.startswith("admin:role:status:"))
async def start_change_role_status(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    role_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        role = await _load_role_for_edit(callback.from_user.id, role_id)

        if role.get("is_system"):
            await callback.answer(
                _tr("نقش سیستمی قابل غیرفعال‌سازی نیست."),
                show_alert=True,
            )
            return

        await state.clear()
        await state.update_data(
            workflow="role_status_update",
            role=role,
            role_id=role_id,
            role_is_active=not bool(role.get("is_active")),
        )
        await state.set_state(AdminManagementStates.waiting_for_role_reason)
        await callback.message.edit_text(
            _tr("📝 دلیل تغییر وضعیت این نقش را بنویسید."),
            reply_markup=None,
        )
        await callback.message.answer(
            _tr("دلیل را ارسال کنید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.message(StateFilter(AdminManagementStates.waiting_for_role_reason))
async def receive_role_change_reason(
    message: Message,
    state: FSMContext,
) -> None:
    reason = str(message.text or "").strip()

    if not 3 <= len(reason) <= 500:
        await message.answer(
            _tr("❌ دلیل باید بین ۳ تا ۵۰۰ کاراکتر باشد."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(reason=reason)
    data = await state.get_data()
    workflow = data.get("workflow")
    role = data.get("role", {})

    if workflow == "role_create":
        summary = (
            f"{_tr('➕ ساخت نقش سفارشی\nنام: <b>')}{html.escape(str(data['role_name']))}{_tr('</b>\nکد: <code>')}{html.escape(str(data['role_code']))}{_tr('</code>\nتعداد دسترسی\u200cها: <code>')}{len(data.get('selected_permission_codes', []))}</code>"
        )
    elif workflow == "role_name_update":
        summary = (
            f"{_tr('✏️ تغییر نام نقش <code>')}{html.escape(str(role['code']))}{_tr('</code>\nنام جدید: <b>')}{html.escape(str(data['role_name']))}</b>"
        )
    elif workflow == "role_description_update":
        description = data.get("role_description") or _tr("بدون توضیح")
        summary = (
            f"{_tr('📝 تغییر توضیح نقش <code>')}{html.escape(str(role['code']))}{_tr('</code>\nتوضیح جدید: ')}{html.escape(str(description))}"
        )
    elif workflow == "role_permissions_update":
        summary = (
            f"{_tr('🔐 تغییر دسترسی\u200cهای نقش <code>')}{html.escape(str(role['code']))}{_tr('</code>\nتعداد دسترسی\u200cهای جدید: <code>')}{len(data.get('selected_permission_codes', []))}</code>"
        )
    else:
        summary = (
            f"{_tr('🚦 تغییر وضعیت نقش <code>')}{html.escape(str(role['code']))}{_tr('</code>\nوضعیت جدید: <b>')}{(_tr('فعال') if data.get('role_is_active') else _tr('غیرفعال'))}</b>"
        )

    await state.set_state(AdminManagementStates.confirming_role_change)
    await message.answer(
        (
            f"{_tr('<b>مرور تغییر نقش</b>\n\n')}{summary}{_tr('\n\nدلیل: ')}{html.escape(reason)}"
        ),
        parse_mode="HTML",
        reply_markup=build_change_confirmation_keyboard(dangerous=False),
    )


@router.callback_query(
    StateFilter(AdminManagementStates.confirming_role_change),
    F.data == "admin:change:confirm",
)
async def confirm_role_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    data = await state.get_data()
    workflow = data.get("workflow")

    try:
        if workflow == "role_create":
            role = await create_admin_role(
                actor_telegram_id=callback.from_user.id,
                code=str(data["role_code"]),
                name=str(data["role_name"]),
                description=data.get("role_description"),
                permission_codes=list(
                    data.get("selected_permission_codes", [])
                ),
                reason=str(data["reason"]),
            )
        else:
            changes: dict = {}

            if workflow == "role_name_update":
                changes["name"] = str(data["role_name"])
            elif workflow == "role_description_update":
                changes["description"] = data.get("role_description")
                changes["description_supplied"] = True
            elif workflow == "role_permissions_update":
                changes["permission_codes"] = list(
                    data.get("selected_permission_codes", [])
                )
            elif workflow == "role_status_update":
                changes["is_active"] = bool(data.get("role_is_active"))

            role = await update_admin_role(
                actor_telegram_id=callback.from_user.id,
                role_id=int(data["role_id"]),
                reason=str(data["reason"]),
                **changes,
            )

        await state.clear()
        await callback.message.edit_text(
            _tr("✅ تغییر نقش ثبت شد.\n\n") + _admin_role_text(role),
            parse_mode="HTML",
            reply_markup=build_admin_role_detail_keyboard(role),
        )
        await callback.answer(_tr("ثبت شد"))
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:plans")
async def show_admin_plans(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "plans.manage"):
        await callback.answer(_tr("دسترسی مدیریت پلن‌ها ندارید."), show_alert=True)
        return

    try:
        await state.clear()
        await _show_plans(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:plan:\d+$"))
async def show_admin_plan_detail(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "plans.manage"):
        await callback.answer(_tr("دسترسی مدیریت پلن‌ها ندارید."), show_alert=True)
        return

    try:
        plan_id = int(callback.data.rsplit(":", 1)[-1])
        await _show_plan_detail(
            callback.message,
            actor_telegram_id=callback.from_user.id,
            plan_id=plan_id,
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:plan:add")
async def start_create_plan(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "plans.manage"):
        await callback.answer(_tr("دسترسی ایجاد پلن ندارید."), show_alert=True)
        return

    await state.clear()
    await state.update_data(workflow="plan_create")
    await state.set_state(AdminManagementStates.waiting_for_plan_name)
    await callback.message.edit_text(
        (
            _tr("➕ <b>ایجاد پلن جدید</b>\n\n"
            "نام نمایشی پلن را ارسال کنید؛ مثلاً «ویژه ۴۵ روزه».")
        ),
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.message.answer(
        _tr("نام پلن را بفرستید:"),
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_name))
async def receive_plan_name(message: Message, state: FSMContext) -> None:
    name = " ".join(str(message.text or "").split())

    if not 2 <= len(name) <= 100:
        await message.answer(
            _tr("❌ نام پلن باید بین ۲ تا ۱۰۰ کاراکتر باشد."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(name=name)
    await state.set_state(AdminManagementStates.waiting_for_plan_name_en)
    await message.answer(
        (
            _tr("🌐 نام انگلیسی همین پلن را وارد کنید؛ "
            "مثلاً Silver 30 Days:")
        ),
        reply_markup=ForceReply(selective=True),
    )


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_name_en))
async def receive_plan_name_en(message: Message, state: FSMContext) -> None:
    name_en = " ".join(str(message.text or "").split())

    if not 2 <= len(name_en) <= 100 or not re.search(r"[A-Za-z]", name_en):
        await message.answer(
            (
                _tr("❌ نام انگلیسی باید بین ۲ تا ۱۰۰ کاراکتر و "
                "دارای حداقل یک حرف انگلیسی باشد.")
            ),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(name_en=name_en)
    await state.set_state(AdminManagementStates.waiting_for_plan_duration)
    await message.answer(
        _tr("📅 مدت اعتبار پلن را به روز وارد کنید؛ مثلاً 30:"),
        reply_markup=ForceReply(selective=True),
    )


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_duration))
async def receive_plan_duration(message: Message, state: FSMContext) -> None:
    duration_days = _parse_plan_integer(str(message.text or ""))

    if duration_days is None or not 1 <= duration_days <= 3650:
        await message.answer(
            _tr("❌ مدت باید عددی بین ۱ تا ۳۶۵۰ روز باشد."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(duration_days=duration_days)
    await state.set_state(AdminManagementStates.waiting_for_plan_price)
    await message.answer(
        _tr("💰 مبلغ پلن را به تومان وارد کنید؛ مثلاً 79000:"),
        reply_markup=ForceReply(selective=True),
    )


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_price))
async def receive_plan_price(message: Message, state: FSMContext) -> None:
    price = _parse_plan_integer(str(message.text or ""))

    if price is None or price <= 0 or price > 9_999_999_999:
        await message.answer(
            _tr("❌ مبلغ باید یک عدد صحیح بزرگ‌تر از صفر باشد."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(price=price)
    await state.set_state(AdminManagementStates.waiting_for_plan_price_usdt)
    await message.answer(
        (
            _tr("💵 مبلغ بین‌المللی همین پلن را به USDT وارد کنید؛ مثلاً 2.5.\n"
            "اگر فعلاً نمی‌خواهید این پلن برای کاربران انگلیسی نمایش داده شود، - بفرستید:")
        ),
        reply_markup=ForceReply(selective=True),
    )


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_price_usdt))
async def receive_plan_price_usdt(message: Message, state: FSMContext) -> None:
    raw_value = str(message.text or "").strip()
    price_usdt = None if raw_value in {"-", "۰", "0"} else _parse_plan_decimal(raw_value)

    if raw_value not in {"-", "۰", "0"} and (
        price_usdt is None or price_usdt <= 0 or price_usdt > Decimal("99999999")
    ):
        await message.answer(
            _tr("❌ مبلغ USDT باید عددی بزرگ‌تر از صفر با حداکثر ۴ رقم اعشار باشد؛ یا - بفرستید."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(
        price_usdt=(str(price_usdt) if price_usdt is not None else None),
    )
    await state.set_state(AdminManagementStates.waiting_for_plan_daily_limit)
    await message.answer(
        (
            _tr("📊 حداکثر خروجی موفق روزانه را وارد کنید.\n"
            "برای نامحدود عدد 0 را بفرستید:")
        ),
        reply_markup=ForceReply(selective=True),
    )


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_daily_limit))
async def receive_plan_daily_limit(message: Message, state: FSMContext) -> None:
    daily_limit = _parse_plan_integer(str(message.text or ""))

    if daily_limit is None or daily_limit > 1_000_000:
        await message.answer(
            _tr("❌ یک عدد بین ۰ تا ۱٬۰۰۰٬۰۰۰ وارد کنید."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(
        daily_download_limit=(daily_limit or None),
    )
    await state.set_state(AdminManagementStates.waiting_for_plan_file_size)
    await message.answer(
        _tr("📦 حداکثر حجم هر خروجی را به MB وارد کنید (۱ تا ۱۹۰۰):"),
        reply_markup=ForceReply(selective=True),
    )


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_file_size))
async def receive_plan_file_size(message: Message, state: FSMContext) -> None:
    file_size = _parse_plan_integer(str(message.text or ""))

    if file_size is None or not 1 <= file_size <= 1900:
        await message.answer(
            _tr("❌ حجم باید عددی بین ۱ تا ۱۹۰۰ مگابایت باشد."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(max_file_size_mb=file_size)
    await state.set_state(AdminManagementStates.selecting_plan_quality)
    await message.answer(
        _tr("🎞 حداکثر کیفیت مجاز را انتخاب کنید:"),
        reply_markup=build_plan_quality_keyboard(mode="create"),
    )


@router.callback_query(
    StateFilter(AdminManagementStates.selecting_plan_quality),
    F.data.startswith("admin:plan:choice:create:quality:"),
)
async def select_plan_quality(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    quality = int(callback.data.rsplit(":", 1)[-1])
    await state.update_data(max_quality=quality)
    await state.set_state(AdminManagementStates.selecting_plan_concurrency)
    await callback.message.edit_text(
        _tr("⚙️ تعداد دانلود هم‌زمان را انتخاب کنید:"),
        reply_markup=build_plan_concurrency_keyboard(mode="create"),
    )
    await callback.answer()


@router.callback_query(
    StateFilter(AdminManagementStates.selecting_plan_concurrency),
    F.data.startswith("admin:plan:choice:create:concurrency:"),
)
async def select_plan_concurrency(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    concurrency = int(callback.data.rsplit(":", 1)[-1])
    await state.update_data(max_concurrent_downloads=concurrency)
    await state.set_state(AdminManagementStates.selecting_plan_priority)
    await callback.message.edit_text(
        _tr("🚀 آیا این پلن اولویت پردازش بالا داشته باشد؟"),
        reply_markup=build_plan_boolean_keyboard(
            mode="create",
            field="priority",
        ),
    )
    await callback.answer()


@router.callback_query(
    StateFilter(AdminManagementStates.selecting_plan_priority),
    F.data.startswith("admin:plan:choice:create:priority:"),
)
async def select_plan_priority(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    enabled = callback.data.rsplit(":", 1)[-1] == "yes"
    await state.update_data(priority_processing=enabled)
    await state.set_state(AdminManagementStates.selecting_plan_forced_join)
    await callback.message.edit_text(
        _tr("📣 آیا کاربران این پلن مشمول عضویت اجباری باشند؟"),
        reply_markup=build_plan_boolean_keyboard(
            mode="create",
            field="forced_join",
        ),
    )
    await callback.answer()


@router.callback_query(
    StateFilter(AdminManagementStates.selecting_plan_forced_join),
    F.data.startswith("admin:plan:choice:create:forced_join:"),
)
async def select_plan_forced_join(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    enabled = callback.data.rsplit(":", 1)[-1] == "yes"
    await state.update_data(forced_join_required=enabled)
    await state.set_state(AdminManagementStates.waiting_for_plan_description)
    await callback.message.edit_text(
        _tr("📝 توضیح کوتاه پلن را بفرستید؛ برای بدون توضیح، فقط - بفرستید."),
        reply_markup=None,
    )
    await callback.message.answer(
        _tr("توضیح پلن:"),
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_description))
async def receive_plan_description(message: Message, state: FSMContext) -> None:
    description = str(message.text or "").strip()

    if len(description) > 2000:
        await message.answer(
            _tr("❌ توضیح پلن حداکثر ۲۰۰۰ کاراکتر است."),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(description=None if description == "-" else description)
    await state.set_state(AdminManagementStates.waiting_for_plan_description_en)
    await message.answer(
        _tr("🌐 توضیح انگلیسی پلن را بفرستید؛ برای بدون توضیح، فقط - بفرستید."),
        reply_markup=ForceReply(selective=True),
    )


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_description_en))
async def receive_plan_description_en(message: Message, state: FSMContext) -> None:
    description_en = str(message.text or "").strip()
    if len(description_en) > 2000:
        await message.answer(
            _tr("❌ توضیح انگلیسی پلن حداکثر ۲۰۰۰ کاراکتر است."),
            reply_markup=ForceReply(selective=True),
        )
        return
    if description_en != "-" and re.search(r"[\u0600-\u06ff]", description_en):
        await message.answer(
            _tr("❌ توضیح انگلیسی نباید شامل متن فارسی باشد."),
            reply_markup=ForceReply(selective=True),
        )
        return
    await state.update_data(description_en=None if description_en == "-" else description_en)
    data = await state.get_data()
    await state.set_state(AdminManagementStates.confirming_plan_create)
    await message.answer(
        _plan_create_summary(data),
        parse_mode="HTML",
        reply_markup=build_plan_confirmation_keyboard(action="create"),
    )


@router.callback_query(
    StateFilter(AdminManagementStates.confirming_plan_create),
    F.data == "admin:plan:create:confirm",
)
async def confirm_create_plan(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    data = await state.get_data()
    plan_payload = {
        "name": data["name"],
        "name_en": data["name_en"],
        "description": data.get("description"),
        "description_en": data.get("description_en"),
        "duration_days": data["duration_days"],
        "price": data["price"],
        "price_usdt": data.get("price_usdt"),
        "daily_download_limit": data.get("daily_download_limit"),
        "max_file_size_mb": data["max_file_size_mb"],
        "max_quality": data["max_quality"],
        "max_concurrent_downloads": data["max_concurrent_downloads"],
        "priority_processing": data["priority_processing"],
        "forced_join_required": data["forced_join_required"],
        "sort_order": 0,
        "is_active": True,
    }

    try:
        plan = await create_admin_plan(
            actor_telegram_id=callback.from_user.id,
            plan=plan_payload,
            reason=_tr("ایجاد پلن از پنل مدیریت تلگرام"),
        )
        await state.clear()
        await callback.message.edit_text(
            _tr("✅ پلن با موفقیت ایجاد شد.\n\n") + _admin_plan_text(plan),
            parse_mode="HTML",
            reply_markup=build_admin_plan_detail_keyboard(plan),
        )
        await callback.answer(_tr("پلن ایجاد شد"))
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(
    F.data.regexp(
        r"^admin:plan:edit:(name|name_en|description|description_en|duration|price|usdt|daily|size|quality|concurrency|order):\d+$"
    )
)
async def start_edit_plan_field(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    parts = callback.data.split(":")
    field = parts[3]
    plan_id = int(parts[4])

    try:
        plan = await get_admin_plan(
            actor_telegram_id=callback.from_user.id,
            plan_id=plan_id,
        )
        await state.clear()
        await state.update_data(
            workflow="plan_update",
            plan=plan,
            plan_id=plan_id,
            plan_edit_field=field,
        )
        await state.set_state(AdminManagementStates.waiting_for_plan_edit_value)

        if field == "quality":
            await callback.message.edit_text(
                _tr("🎞 حداکثر کیفیت جدید را انتخاب کنید:"),
                reply_markup=build_plan_quality_keyboard(mode="edit"),
            )
        elif field == "concurrency":
            await callback.message.edit_text(
                _tr("⚙️ تعداد دانلود هم‌زمان جدید را انتخاب کنید:"),
                reply_markup=build_plan_concurrency_keyboard(mode="edit"),
            )
        else:
            prompts = {
                "name": _tr("نام فارسی جدید پلن را بفرستید:"),
                "name_en": _tr("نام انگلیسی جدید پلن را بفرستید؛ مثلاً Silver 30 Days:"),
                "description": _tr("توضیح جدید را بفرستید؛ برای حذف توضیح، - بفرستید:"),
                "description_en": _tr("توضیح انگلیسی جدید را بفرستید؛ برای حذف توضیح، - بفرستید:"),
                "duration": _tr("مدت جدید را به روز وارد کنید:"),
                "price": _tr("مبلغ جدید را به تومان وارد کنید:"),
                "usdt": _tr("مبلغ جدید USDT را وارد کنید؛ برای غیرفعال‌کردن فروش بین‌المللی - بفرستید:"),
                "daily": _tr("سقف روزانه جدید را بفرستید؛ 0 یعنی نامحدود:"),
                "size": _tr("حداکثر حجم جدید را به MB وارد کنید (۱ تا ۱۹۰۰):"),
                "order": _tr("ترتیب نمایش را وارد کنید؛ عدد کوچک‌تر بالاتر نمایش داده می‌شود:"),
            }
            await callback.message.edit_text(prompts[field], reply_markup=None)
            await callback.message.answer(
                _tr("مقدار جدید را ارسال کنید:"),
                reply_markup=ForceReply(selective=True),
            )

        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.message(StateFilter(AdminManagementStates.waiting_for_plan_edit_value))
async def receive_plan_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = str(data.get("plan_edit_field") or "")

    if field in {"quality", "concurrency"}:
        return

    raw_value = str(message.text or "").strip()
    changes: dict = {}
    error: str | None = None

    if field == "name":
        normalized_name = " ".join(raw_value.split())
        if 2 <= len(normalized_name) <= 100:
            changes["name"] = normalized_name
        else:
            error = _tr("نام فارسی باید بین ۲ تا ۱۰۰ کاراکتر باشد.")
    elif field == "name_en":
        normalized_name = " ".join(raw_value.split())
        if 2 <= len(normalized_name) <= 100 and re.search(
            r"[A-Za-z]",
            normalized_name,
        ):
            changes["name_en"] = normalized_name
        else:
            error = (
                _tr("نام انگلیسی باید بین ۲ تا ۱۰۰ کاراکتر و "
                "دارای حداقل یک حرف انگلیسی باشد.")
            )
    elif field == "description":
        if len(raw_value) <= 2000:
            changes["description"] = None if raw_value == "-" else raw_value
        else:
            error = _tr("توضیح حداکثر ۲۰۰۰ کاراکتر است.")
    elif field == "description_en":
        if re.search(r"[\u0600-\u06ff]", raw_value):
            error = _tr("توضیح انگلیسی نباید شامل متن فارسی باشد.")
        elif len(raw_value) <= 2000:
            changes["description_en"] = None if raw_value == "-" else raw_value
        else:
            error = _tr("توضیح انگلیسی حداکثر ۲۰۰۰ کاراکتر است.")
    elif field == "usdt":
        if raw_value in {"-", "۰", "0"}:
            changes["price_usdt"] = None
        else:
            amount = _parse_plan_decimal(raw_value)
            if amount is None or amount <= 0 or amount > Decimal("99999999"):
                error = _tr("مبلغ USDT معتبر نیست؛ حداکثر ۴ رقم اعشار مجاز است.")
            else:
                changes["price_usdt"] = str(amount)
    else:
        number = _parse_plan_integer(raw_value)

        if number is None:
            error = _tr("مقدار باید فقط عدد باشد.")
        elif field == "duration" and not 1 <= number <= 3650:
            error = _tr("مدت باید بین ۱ تا ۳۶۵۰ روز باشد.")
        elif field == "price" and number > 9_999_999_999:
            error = _tr("مبلغ واردشده بیش از حد مجاز است.")
        elif field == "daily" and number > 1_000_000:
            error = _tr("سقف روزانه بیش از حد مجاز است.")
        elif field == "size" and not 1 <= number <= 1900:
            error = _tr("حجم باید بین ۱ تا ۱۹۰۰ مگابایت باشد.")
        elif field == "order" and number > 100_000:
            error = _tr("ترتیب نمایش باید بین ۰ تا ۱۰۰٬۰۰۰ باشد.")
        elif field == "duration":
            changes["duration_days"] = number
        elif field == "price":
            changes["price"] = number
        elif field == "daily":
            changes["daily_download_limit"] = number or None
        elif field == "size":
            changes["max_file_size_mb"] = number
        elif field == "order":
            changes["sort_order"] = number

    if error:
        await message.answer(
            f"❌ {error}",
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(plan_changes=changes)
    await state.set_state(AdminManagementStates.confirming_plan_update)
    await message.answer(
        _plan_update_summary(data["plan"], changes),
        parse_mode="HTML",
        reply_markup=build_plan_confirmation_keyboard(action="update"),
    )


@router.callback_query(
    StateFilter(AdminManagementStates.waiting_for_plan_edit_value),
    F.data.regexp(r"^admin:plan:choice:edit:(quality|concurrency):\d+$"),
)
async def select_plan_edit_choice(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    parts = callback.data.split(":")
    field = parts[4]
    value = int(parts[5])
    data = await state.get_data()
    expected_field = str(data.get("plan_edit_field") or "")

    if field != expected_field:
        await callback.answer(_tr("این فرم منقضی شده است."), show_alert=True)
        return

    changes = {
        "max_quality" if field == "quality" else "max_concurrent_downloads": value
    }
    await state.update_data(plan_changes=changes)
    await state.set_state(AdminManagementStates.confirming_plan_update)
    await callback.message.edit_text(
        _plan_update_summary(data["plan"], changes),
        parse_mode="HTML",
        reply_markup=build_plan_confirmation_keyboard(action="update"),
    )
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"^admin:plan:toggle:(priority|forced_join|active):\d+$")
)
async def toggle_plan_boolean(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    parts = callback.data.split(":")
    field = parts[3]
    plan_id = int(parts[4])
    field_map = {
        "priority": "priority_processing",
        "forced_join": "forced_join_required",
        "active": "is_active",
    }

    try:
        plan = await get_admin_plan(
            actor_telegram_id=callback.from_user.id,
            plan_id=plan_id,
        )
        target_field = field_map[field]
        changes = {target_field: not bool(plan.get(target_field))}
        await state.clear()
        await state.update_data(
            workflow="plan_update",
            plan=plan,
            plan_id=plan_id,
            plan_changes=changes,
        )
        await state.set_state(AdminManagementStates.confirming_plan_update)
        await callback.message.edit_text(
            _plan_update_summary(plan, changes),
            parse_mode="HTML",
            reply_markup=build_plan_confirmation_keyboard(action="update"),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:plan:delete:\d+$"))
async def start_soft_delete_plan(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    plan_id = int(callback.data.rsplit(":", 1)[-1])

    try:
        plan = await get_admin_plan(
            actor_telegram_id=callback.from_user.id,
            plan_id=plan_id,
        )
        changes = {"is_deleted": True}
        await state.clear()
        await state.update_data(
            workflow="plan_update",
            plan=plan,
            plan_id=plan_id,
            plan_changes=changes,
        )
        await state.set_state(AdminManagementStates.confirming_plan_update)
        await callback.message.edit_text(
            (
                _tr("⚠️ این عملیات پلن را غیرفعال و از فهرست فروش مخفی می‌کند.\n\n")
                + _plan_update_summary(plan, changes)
            ),
            parse_mode="HTML",
            reply_markup=build_plan_confirmation_keyboard(action="update"),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(
    StateFilter(AdminManagementStates.confirming_plan_update),
    F.data == "admin:plan:update:confirm",
)
async def confirm_plan_update(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    data = await state.get_data()
    changes = dict(data.get("plan_changes") or {})

    try:
        plan = await update_admin_plan(
            actor_telegram_id=callback.from_user.id,
            plan_id=int(data["plan_id"]),
            changes=changes,
            reason=_tr("ویرایش پلن از پنل مدیریت تلگرام"),
        )
        await state.clear()

        if plan.get("is_deleted"):
            await _show_plans(callback.message, callback.from_user.id)
        else:
            await callback.message.edit_text(
                _tr("✅ تغییر پلن ثبت شد.\n\n") + _admin_plan_text(plan),
                parse_mode="HTML",
                reply_markup=build_admin_plan_detail_keyboard(plan),
            )

        await callback.answer(_tr("ثبت شد"))
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:plan:cancel")
async def cancel_plan_workflow(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    plan_id = data.get("plan_id")
    await state.clear()

    if not isinstance(callback.message, Message):
        return

    try:
        if plan_id is not None:
            await _show_plan_detail(
                callback.message,
                actor_telegram_id=callback.from_user.id,
                plan_id=int(plan_id),
            )
        else:
            await _show_plans(callback.message, callback.from_user.id)

        await callback.answer(_tr("لغو شد"))
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:workflow:cancel")
async def cancel_admin_workflow(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    workflow = str(data.get("workflow") or "")
    await state.clear()

    if not isinstance(callback.message, Message):
        return

    try:
        if workflow.startswith("role_"):
            await _show_roles(callback.message, callback.from_user.id)
        else:
            await _show_accounts(callback.message, callback.from_user.id)

        await callback.answer(_tr("لغو شد"))
    except BackendAPIError as exc:
        await callback.answer(_backend_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:close")
async def close_admin_panel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    context = await _context_or_none(callback.from_user.id)
    await state.clear()

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _tr("پنل مدیریت بسته شد."),
            reply_markup=build_home_keyboard(
                include_admin=context is not None,
            ),
        )

    await callback.answer()
