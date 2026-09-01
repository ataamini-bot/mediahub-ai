import html
import re

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.keyboards.admin import (
    build_admin_account_detail_keyboard,
    build_admin_accounts_keyboard,
    build_admin_back_keyboard,
    build_admin_home_keyboard,
    build_admin_role_detail_keyboard,
    build_admin_roles_keyboard,
    build_change_confirmation_keyboard,
    build_final_danger_confirmation_keyboard,
    build_permission_picker_keyboard,
    build_role_picker_keyboard,
)
from app.keyboards.payment import build_home_keyboard
from app.services.backend import (
    BackendAPIError,
    create_admin_account,
    create_admin_role,
    get_admin_account,
    get_admin_context,
    list_admin_accounts,
    list_admin_permissions,
    list_admin_roles,
    list_application_settings,
    register_telegram_user,
    update_admin_account,
    update_admin_role,
)
from app.state.admin import AdminManagementStates


router = Router(name="admin")


def _can(context: dict, permission: str) -> bool:
    return bool(
        context.get("is_superadmin")
        or permission in set(context.get("permissions", []))
    )


def _admin_panel_text(context: dict) -> str:
    role_names = context.get("roles", [])
    role_text = ", ".join(role_names) if role_names else "Superadmin"
    return (
        "⚙️ <b>پنل مدیریت MediaHub AI</b>\n\n"
        f"👮 نقش: <code>{html.escape(role_text)}</code>\n"
        f"🔐 تعداد دسترسی‌ها: <code>{len(context.get('permissions', []))}</code>"
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
            "کاربر پیدا نشد. کاربر باید ابتدا ربات را Start کرده باشد."
        ),
        "admin_account_not_found": "حساب مدیر پیدا نشد.",
        "admin_account_conflict": "این کاربر هم‌اکنون مدیر فعال است.",
        "last_superadmin": (
            "آخرین Superadmin فعال را نمی‌توان غیرفعال یا تنزل داد."
        ),
        "admin_role_not_found": "یک یا چند نقش انتخاب‌شده معتبر نیست.",
        "admin_role_conflict": "کد این نقش قبلاً ثبت شده است.",
        "admin_role_validation": "ترکیب نقش یا دسترسی معتبر نیست.",
        "admin_role_in_use": (
            "این تغییر بعضی مدیران را بدون دسترسی پنل می‌گذارد؛ "
            "ابتدا نقش آن مدیران را عوض کنید."
        ),
        "system_role_protected": "نقش سیستمی قابل غیرفعال‌سازی نیست.",
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
    parts = [full_name or "بدون نام"]

    if username:
        parts.append(f"@{username}")

    return " — ".join(parts)


def _admin_account_text(account: dict) -> str:
    roles = account.get("roles", [])
    role_text = ", ".join(
        str(role.get("name") or role.get("code"))
        for role in roles
    )[:1200] or "بدون نقش"
    status = "فعال ✅" if account.get("is_active") else "غیرفعال ⛔️"
    authority = (
        "Superadmin 👑" if account.get("is_superadmin") else "Admin 👮"
    )
    return (
        "👤 <b>مشخصات مدیر</b>\n\n"
        f"نام: {html.escape(_account_identity(account))}\n"
        f"Telegram ID: <code>{int(account['telegram_id'])}</code>\n"
        f"سطح: <b>{authority}</b>\n"
        f"وضعیت: <b>{status}</b>\n"
        f"نقش‌ها: <code>{html.escape(role_text)}</code>"
    )


def _admin_role_text(role: dict) -> str:
    permissions = role.get("permission_codes", [])
    permission_lines = "\n".join(
        f"• <code>{html.escape(str(code))}</code>"
        for code in permissions[:50]
    ) or "• بدون دسترسی"
    status = "فعال ✅" if role.get("is_active") else "غیرفعال ⛔️"
    kind = "سیستمی 🔒" if role.get("is_system") else "سفارشی 🧩"
    description = role.get("description") or "—"
    return (
        "🔐 <b>مشخصات نقش</b>\n\n"
        f"نام: <b>{html.escape(str(role['name']))}</b>\n"
        f"کد: <code>{html.escape(str(role['code']))}</code>\n"
        f"نوع: {kind}\n"
        f"وضعیت: {status}\n"
        f"مدیران دارای نقش: <code>{int(role.get('assignment_count', 0))}</code>\n"
        f"توضیح: {html.escape(str(description))}\n\n"
        f"<b>دسترسی‌ها:</b>\n{permission_lines}"
    )


async def _show_accounts(message: Message, actor_telegram_id: int) -> None:
    accounts = await list_admin_accounts(actor_telegram_id)
    active_count = sum(bool(row.get("is_active")) for row in accounts)
    await message.edit_text(
        (
            "👮 <b>مدیریت مدیران</b>\n\n"
            f"تعداد کل: <code>{len(accounts)}</code>\n"
            f"فعال: <code>{active_count}</code>\n\n"
            "برای مشاهده یا ویرایش، یک مدیر را انتخاب کنید."
        ),
        parse_mode="HTML",
        reply_markup=build_admin_accounts_keyboard(accounts),
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
            "🔐 <b>نقش‌ها و سطح دسترسی</b>\n\n"
            "🔒 نقش سیستمی، 🧩 نقش سفارشی\n"
            "برای مشاهده یا ویرایش، یک نقش را انتخاب کنید."
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
        str(row.get("name") or row.get("code"))
        for row in rows
        if str(row.get("code")) in selected_codes
    ) or "بدون نقش"


async def _edit_role_picker(message: Message, data: dict) -> None:
    roles = list(data.get("available_roles", []))
    selected_codes = set(data.get("selected_role_codes", []))
    await message.edit_text(
        (
            "🔐 <b>انتخاب نقش‌ها</b>\n\n"
            "هر مدیر می‌تواند هم‌زمان چند نقش داشته باشد. "
            "روی نقش‌ها بزنید و سپس ادامه را انتخاب کنید."
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
            "🔐 <b>انتخاب دسترسی‌ها</b>\n\n"
            "دسترسی‌های موردنیاز را انتخاب کنید و سپس ادامه را بزنید."
        ),
        parse_mode="HTML",
        reply_markup=build_permission_picker_keyboard(
            permissions,
            selected_codes,
        ),
    )


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    try:
        await register_telegram_user(message)
    except Exception:
        await message.answer("❌ اتصال به Backend برقرار نشد.")
        return

    context = await _context_or_none(message.from_user.id)

    if context is None:
        await message.answer("⛔️ شما به پنل مدیریت دسترسی ندارید.")
        return

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


@router.callback_query(F.data == "admin:open")
async def open_admin_panel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None:
        await callback.answer("دسترسی مدیریت ندارید.", show_alert=True)
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
        await callback.answer("دسترسی مشاهده تنظیمات ندارید.", show_alert=True)
        return

    try:
        await state.clear()
        settings_rows = await list_application_settings(
            callback.from_user.id
        )
    except BackendAPIError:
        await callback.answer("دریافت تنظیمات ممکن نشد.", show_alert=True)
        return

    if not settings_rows:
        body = (
            "⚙️ <b>تنظیمات ربات</b>\n\n"
            "هنوز هیچ تنظیم تجاری ثبت نشده است. "
            "فرم‌های افزودن تنظیمات در مرحله بعد فعال می‌شوند."
        )
    else:
        lines = ["⚙️ <b>تنظیمات ربات</b>", ""]

        for row in settings_rows[:30]:
            state = "✅" if row.get("is_configured") else "⚠️"
            sensitive = " 🔒" if row.get("is_sensitive") else ""
            lines.append(
                f"{state} <code>{html.escape(str(row.get('key')))}</code>"
                f"{sensitive} — v{int(row.get('version', 1))}"
            )

        body = "\n".join(lines)

    await callback.message.edit_text(
        body,
        parse_mode="HTML",
        reply_markup=build_admin_back_keyboard(),
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

    if context is None or not _can(context, "admins.manage"):
        await callback.answer("دسترسی مدیریت مدیران ندارید.", show_alert=True)
        return

    try:
        await state.clear()
        await _show_accounts(callback.message, callback.from_user.id)
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
        await callback.answer("دسترسی مدیریت مدیران ندارید.", show_alert=True)
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
                "➕ <b>افزودن مدیر</b>\n\n"
                "Telegram ID عددی کاربر را بفرستید.\n"
                "کاربر باید قبلاً ربات را Start کرده باشد."
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            "Telegram ID را ارسال کنید:",
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
            "❌ Telegram ID باید فقط عدد باشد. دوباره ارسال کنید.",
            reply_markup=ForceReply(selective=True),
        )
        return

    if target_telegram_id <= 0:
        await message.answer(
            "❌ Telegram ID معتبر نیست. دوباره ارسال کنید.",
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(target_telegram_id=target_telegram_id)
    await state.set_state(AdminManagementStates.selecting_admin_roles)
    data = await state.get_data()
    roles = list(data.get("available_roles", []))
    await message.answer(
        (
            "🔐 <b>انتخاب نقش‌ها</b>\n\n"
            "هر مدیر می‌تواند هم‌زمان چند نقش داشته باشد. "
            "روی نقش‌ها بزنید و سپس ادامه را انتخاب کنید."
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
                "برای Admin معمولی حداقل یک نقش انتخاب کنید.",
                show_alert=True,
            )
            return

        await state.set_state(AdminManagementStates.waiting_for_admin_reason)
        await callback.message.edit_text(
            (
                "📝 <b>دلیل تغییر</b>\n\n"
                "دلیل افزودن یا تغییر دسترسی این مدیر را بنویسید. "
                "این متن در Audit Log ثبت می‌شود."
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            "دلیل را ارسال کنید:",
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
        return

    if choice == "super":
        if data.get("workflow") != "admin_create":
            await callback.answer("این گزینه در این فرم فعال نیست.")
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
        await callback.answer("انتخاب نامعتبر است.", show_alert=True)
        return

    roles = list(data.get("available_roles", []))
    role = next((row for row in roles if int(row["id"]) == role_id), None)

    if role is None:
        await callback.answer("نقش پیدا نشد.", show_alert=True)
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
        await callback.answer("دسترسی مدیریت مدیران ندارید.", show_alert=True)
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
        await callback.answer("دسترسی مدیریت مدیران ندارید.", show_alert=True)
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
        await callback.answer("دسترسی مدیریت مدیران ندارید.", show_alert=True)
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
                "⚠️ <b>تغییر سطح Superadmin</b>\n\n"
                "دلیل این تغییر حساس را بنویسید."
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            "دلیل را ارسال کنید:",
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
        await callback.answer("دسترسی مدیریت مدیران ندارید.", show_alert=True)
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
                "📝 <b>تغییر وضعیت مدیر</b>\n\n"
                "دلیل فعال‌سازی یا غیرفعال‌سازی را بنویسید."
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            "دلیل را ارسال کنید:",
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
            "❌ دلیل باید حداقل ۳ کاراکتر باشد. دوباره بفرستید.",
            reply_markup=ForceReply(selective=True),
        )
        return

    if len(reason) > 500:
        await message.answer(
            "❌ دلیل حداکثر ۵۰۰ کاراکتر است. خلاصه‌تر بفرستید.",
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
            "➕ افزودن مدیر جدید\n"
            f"Telegram ID: <code>{int(data['target_telegram_id'])}</code>\n"
            "نقش‌ها: "
            f"<code>{html.escape(_selected_names(roles, selected))}</code>\n"
            "Superadmin: "
            f"<b>{'بله' if data.get('is_superadmin') else 'خیر'}</b>"
        )
    elif workflow == "admin_roles_update":
        summary = (
            "🔐 ویرایش نقش‌های مدیر\n"
            f"Telegram ID: <code>{int(data['target_telegram_id'])}</code>\n"
            "نقش‌های جدید: "
            f"<code>{html.escape(_selected_names(roles, selected))}</code>"
        )
    elif workflow == "admin_super_update":
        summary = (
            "👑 تغییر سطح Superadmin\n"
            f"Telegram ID: <code>{int(data['target_telegram_id'])}</code>\n"
            f"مقدار جدید: <b>{'بله' if data.get('is_superadmin') else 'خیر'}</b>"
        )
    else:
        summary = (
            "🚦 تغییر وضعیت مدیر\n"
            f"Telegram ID: <code>{int(data['target_telegram_id'])}</code>\n"
            f"وضعیت جدید: <b>{'فعال' if data.get('is_active') else 'غیرفعال'}</b>"
        )

    await state.set_state(AdminManagementStates.confirming_admin_change)
    await message.answer(
        (
            "<b>مرور تغییر</b>\n\n"
            f"{summary}\n\n"
            f"دلیل: {html.escape(reason)}"
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
            "✅ تغییر با موفقیت ثبت شد.\n\n" + _admin_account_text(account),
            parse_mode="HTML",
            reply_markup=build_admin_account_detail_keyboard(account),
        )
        await callback.answer("ثبت شد")
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
                    "🚨 <b>تأیید نهایی تغییر حساس</b>\n\n"
                    "این عملیات می‌تواند دسترسی مدیریتی را تغییر دهد. "
                    "فقط در صورت اطمینان کامل تأیید کنید."
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
        await callback.answer("دسترسی مدیریت نقش‌ها ندارید.", show_alert=True)
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
        await callback.answer("دسترسی مدیریت نقش‌ها ندارید.", show_alert=True)
        return

    await state.clear()
    await state.update_data(workflow="role_create")
    await state.set_state(AdminManagementStates.waiting_for_role_code)
    await callback.message.edit_text(
        (
            "➕ <b>ساخت نقش سفارشی</b>\n\n"
            "یک کد انگلیسی یکتا بنویسید؛ مانند:\n"
            "<code>content_reviewer</code>"
        ),
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.message.answer(
        "کد نقش را ارسال کنید:",
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.message(StateFilter(AdminManagementStates.waiting_for_role_code))
async def receive_role_code(message: Message, state: FSMContext) -> None:
    code = str(message.text or "").strip().lower()

    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,99}", code):
        await message.answer(
            (
                "❌ کد نامعتبر است. با حرف انگلیسی شروع شود و فقط از "
                "حروف کوچک، عدد، نقطه، خط تیره یا زیرخط استفاده کند."
            ),
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(role_code=code)
    await state.set_state(AdminManagementStates.waiting_for_role_name)
    await message.answer(
        "نام نمایشی نقش را بنویسید؛ مثلاً «بازبین محتوا»: ",
        reply_markup=ForceReply(selective=True),
    )


@router.callback_query(F.data.regexp(r"^admin:role:\d+$"))
async def show_admin_role_detail(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "roles.manage"):
        await callback.answer("دسترسی مدیریت نقش‌ها ندارید.", show_alert=True)
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
                "✏️ <b>تغییر نام نقش</b>\n\n"
                f"نام فعلی: <b>{html.escape(str(role['name']))}</b>"
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            "نام جدید را ارسال کنید:",
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
            "❌ نام نقش باید بین ۲ تا ۱۵۰ کاراکتر باشد.",
            reply_markup=ForceReply(selective=True),
        )
        return

    data = await state.get_data()
    await state.update_data(role_name=name)

    if data.get("workflow") == "role_create":
        await state.set_state(AdminManagementStates.waiting_for_role_description)
        await message.answer(
            (
                "توضیح نقش را ارسال کنید. اگر توضیح نمی‌خواهید، "
                "فقط یک خط تیره <code>-</code> بفرستید."
            ),
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.set_state(AdminManagementStates.waiting_for_role_reason)
    await message.answer(
        "دلیل تغییر نام را ارسال کنید:",
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
                "📝 <b>تغییر توضیح نقش</b>\n\n"
                "توضیح جدید را بفرستید؛ برای حذف توضیح، فقط "
                "یک خط تیره <code>-</code> ارسال کنید."
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            "توضیح جدید را ارسال کنید:",
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
            "❌ توضیح نقش حداکثر ۲۰۰۰ کاراکتر است.",
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
                "🔐 <b>انتخاب دسترسی‌ها</b>\n\n"
                "دسترسی‌های موردنیاز را انتخاب کنید و سپس ادامه را بزنید."
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
        "دلیل تغییر توضیح را ارسال کنید:",
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
                "حداقل یک دسترسی انتخاب کنید.",
                show_alert=True,
            )
            return

        await state.set_state(AdminManagementStates.waiting_for_role_reason)
        await callback.message.edit_text(
            (
                "📝 <b>دلیل تغییر</b>\n\n"
                "دلیل ساخت یا تغییر این نقش را بنویسید. "
                "این متن در Audit Log ثبت می‌شود."
            ),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.message.answer(
            "دلیل را ارسال کنید:",
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
        return

    try:
        permission_id = int(choice)
    except ValueError:
        await callback.answer("انتخاب نامعتبر است.", show_alert=True)
        return

    permissions = list(data.get("available_permissions", []))
    permission = next(
        (row for row in permissions if int(row["id"]) == permission_id),
        None,
    )

    if permission is None:
        await callback.answer("دسترسی پیدا نشد.", show_alert=True)
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
                "نقش سیستمی قابل غیرفعال‌سازی نیست.",
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
            "📝 دلیل تغییر وضعیت این نقش را بنویسید.",
            reply_markup=None,
        )
        await callback.message.answer(
            "دلیل را ارسال کنید:",
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
            "❌ دلیل باید بین ۳ تا ۵۰۰ کاراکتر باشد.",
            reply_markup=ForceReply(selective=True),
        )
        return

    await state.update_data(reason=reason)
    data = await state.get_data()
    workflow = data.get("workflow")
    role = data.get("role", {})

    if workflow == "role_create":
        summary = (
            "➕ ساخت نقش سفارشی\n"
            f"نام: <b>{html.escape(str(data['role_name']))}</b>\n"
            f"کد: <code>{html.escape(str(data['role_code']))}</code>\n"
            "تعداد دسترسی‌ها: "
            f"<code>{len(data.get('selected_permission_codes', []))}</code>"
        )
    elif workflow == "role_name_update":
        summary = (
            f"✏️ تغییر نام نقش <code>{html.escape(str(role['code']))}</code>\n"
            f"نام جدید: <b>{html.escape(str(data['role_name']))}</b>"
        )
    elif workflow == "role_description_update":
        description = data.get("role_description") or "بدون توضیح"
        summary = (
            f"📝 تغییر توضیح نقش <code>{html.escape(str(role['code']))}</code>\n"
            f"توضیح جدید: {html.escape(str(description))}"
        )
    elif workflow == "role_permissions_update":
        summary = (
            f"🔐 تغییر دسترسی‌های نقش <code>{html.escape(str(role['code']))}</code>\n"
            "تعداد دسترسی‌های جدید: "
            f"<code>{len(data.get('selected_permission_codes', []))}</code>"
        )
    else:
        summary = (
            f"🚦 تغییر وضعیت نقش <code>{html.escape(str(role['code']))}</code>\n"
            "وضعیت جدید: "
            f"<b>{'فعال' if data.get('role_is_active') else 'غیرفعال'}</b>"
        )

    await state.set_state(AdminManagementStates.confirming_role_change)
    await message.answer(
        (
            "<b>مرور تغییر نقش</b>\n\n"
            f"{summary}\n\n"
            f"دلیل: {html.escape(reason)}"
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
            "✅ تغییر نقش ثبت شد.\n\n" + _admin_role_text(role),
            parse_mode="HTML",
            reply_markup=build_admin_role_detail_keyboard(role),
        )
        await callback.answer("ثبت شد")
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

        await callback.answer("لغو شد")
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
            "پنل مدیریت بسته شد.",
            reply_markup=build_home_keyboard(
                include_admin=context is not None,
            ),
        )

    await callback.answer()
