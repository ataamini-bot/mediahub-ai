import html
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.admin_runtime_settings import (
    RUNTIME_SETTINGS_BY_KEY,
    runtime_settings_text,
    setting_value_text,
)
from app.keyboards.admin_settings import (
    build_runtime_settings_keyboard,
    build_setting_confirmation_keyboard,
    build_setting_value_keyboard,
)
from app.services.backend import (
    BackendAPIError,
    get_admin_context,
    list_application_settings,
    update_application_setting,
)
from app.state.admin_settings import AdminSettingStates


router = Router(name="admin-settings")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


def _can(context: dict, permission: str) -> bool:
    return bool(
        context.get("is_superadmin")
        or permission in set(context.get("permissions", []))
    )


async def _context(telegram_id: int, permission: str) -> dict | None:
    try:
        context = await get_admin_context(telegram_id)
    except BackendAPIError:
        return None
    if not context.get("is_admin") or not _can(context, permission):
        return None
    return context


async def _show_settings(message: Message, actor_telegram_id: int) -> None:
    context = await _context(actor_telegram_id, "settings.view")
    if context is None:
        return
    rows = await list_application_settings(actor_telegram_id)
    await message.edit_text(
        runtime_settings_text(rows),
        parse_mode="HTML",
        reply_markup=build_runtime_settings_keyboard(
            rows,
            can_manage=_can(context, "settings.manage"),
        ),
    )


def _confirmation_text(key: str, value: object) -> str:
    definition = RUNTIME_SETTINGS_BY_KEY[key]
    rendered = setting_value_text(definition, value)
    warning = ""
    if key == "bot.maintenance_mode" and value is True:
        warning = (
            "\n\n⚠️ دانلود و خرید جدید کاربران متوقف می‌شود؛ "
            "پنل مدیریت در دسترس می‌ماند."
        )
    elif key in {"downloads.enabled", "payments.enabled"} and value is False:
        warning = "\n\n⚠️ عملیات جدید کاربران در این بخش متوقف می‌شود."

    return (
        "⚙️ <b>تأیید تغییر تنظیم</b>\n\n"
        f"تنظیم: <b>{html.escape(definition.label)}</b>\n"
        f"مقدار جدید: <code>{html.escape(rendered)}</code>"
        f"{warning}"
    )


async def _prepare_confirmation(
    target: Message,
    state: FSMContext,
    *,
    key: str,
    value: object,
    edit: bool,
) -> None:
    await state.update_data(setting_key=key, setting_value=value)
    await state.set_state(AdminSettingStates.confirming_change)
    method = target.edit_text if edit else target.answer
    await method(
        _confirmation_text(key, value),
        parse_mode="HTML",
        reply_markup=build_setting_confirmation_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:setting:edit:"))
async def edit_runtime_setting(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    context = await _context(callback.from_user.id, "settings.manage")
    if context is None:
        await callback.answer("دسترسی ویرایش تنظیمات ندارید.", show_alert=True)
        return

    key = callback.data.split(":", 3)[-1]
    definition = RUNTIME_SETTINGS_BY_KEY.get(key)
    if definition is None:
        await callback.answer("تنظیم معتبر نیست.", show_alert=True)
        return

    try:
        rows = await list_application_settings(callback.from_user.id)
    except BackendAPIError:
        await callback.answer("دریافت تنظیمات ممکن نشد.", show_alert=True)
        return
    row = next((item for item in rows if item.get("key") == key), None)
    if row is None:
        await callback.answer("تنظیم در دیتابیس پیدا نشد.", show_alert=True)
        return

    await state.clear()
    await state.update_data(setting_row=row, setting_key=key)

    if definition.kind == "boolean":
        await _prepare_confirmation(
            callback.message,
            state,
            key=key,
            value=not bool(row.get("value")),
            edit=True,
        )
    else:
        await state.set_state(AdminSettingStates.waiting_for_value)
        if definition.kind == "integer":
            instruction = (
                f"یک عدد بین {definition.minimum} تا "
                f"{definition.maximum} مگابایت بفرستید."
            )
        else:
            instruction = (
                "نام معتبر منطقه زمانی IANA را بفرستید؛ "
                "مثلاً <code>Asia/Tehran</code>."
            )
        await callback.message.edit_text(
            (
                f"✏️ <b>{html.escape(definition.label)}</b>\n\n"
                f"{instruction}"
            ),
            parse_mode="HTML",
            reply_markup=build_setting_value_keyboard(key),
        )
        await callback.message.answer(
            "مقدار جدید را ارسال کنید:",
            reply_markup=ForceReply(selective=True),
        )
    await callback.answer()


@router.callback_query(
    AdminSettingStates.waiting_for_value,
    F.data.startswith("admin:setting:zone:"),
)
async def choose_setting_timezone(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    values = {
        "tehran": "Asia/Tehran",
        "utc": "UTC",
        "dubai": "Asia/Dubai",
    }
    value = values.get(callback.data.rsplit(":", 1)[-1])
    if value is None:
        await callback.answer("منطقه زمانی معتبر نیست.", show_alert=True)
        return
    await _prepare_confirmation(
        callback.message,
        state,
        key="quota.timezone",
        value=value,
        edit=True,
    )
    await callback.answer()


@router.message(AdminSettingStates.waiting_for_value)
async def receive_setting_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = str(data.get("setting_key") or "")
    definition = RUNTIME_SETTINGS_BY_KEY.get(key)
    if definition is None or not message.text:
        await state.clear()
        return

    raw_value = message.text.strip()
    if definition.kind == "integer":
        translated = raw_value.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
        if not translated.isdigit():
            await message.answer("فقط یک عدد صحیح بفرستید.")
            return
        value: object = int(translated)
        if (
            definition.minimum is not None
            and int(value) < definition.minimum
        ) or (
            definition.maximum is not None
            and int(value) > definition.maximum
        ):
            await message.answer(
                f"عدد باید بین {definition.minimum} تا "
                f"{definition.maximum} باشد."
            )
            return
    else:
        try:
            ZoneInfo(raw_value)
        except (ZoneInfoNotFoundError, ValueError):
            await message.answer(
                "منطقه زمانی معتبر نیست؛ مثلاً Asia/Tehran بفرستید."
            )
            return
        value = raw_value

    await _prepare_confirmation(
        message,
        state,
        key=key,
        value=value,
        edit=False,
    )


@router.callback_query(
    AdminSettingStates.confirming_change,
    F.data == "admin:setting:confirm",
)
async def confirm_setting_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    context = await _context(callback.from_user.id, "settings.manage")
    if context is None:
        await callback.answer("دسترسی ویرایش تنظیمات ندارید.", show_alert=True)
        return

    data = await state.get_data()
    key = str(data.get("setting_key") or "")
    row = data.get("setting_row")
    definition = RUNTIME_SETTINGS_BY_KEY.get(key)
    if definition is None or not isinstance(row, dict):
        await state.clear()
        await callback.answer("درخواست تغییر منقضی شده است.", show_alert=True)
        return

    try:
        await update_application_setting(
            actor_telegram_id=callback.from_user.id,
            key=key,
            category=definition.category,
            value=data.get("setting_value"),
            expected_version=int(row["version"]),
            description=row.get("description"),
        )
        await state.clear()
        await _show_settings(callback.message, callback.from_user.id)
        await callback.answer("تنظیم با موفقیت اعمال شد.")
    except BackendAPIError as exc:
        if exc.status_code == 409:
            message_text = "تنظیم هم‌زمان تغییر کرده؛ صفحه را دوباره باز کنید."
        elif exc.status_code == 422:
            message_text = "مقدار واردشده معتبر نیست."
        else:
            message_text = "ثبت تنظیم انجام نشد."
        await callback.answer(message_text, show_alert=True)


@router.callback_query(F.data == "admin:setting:cancel")
async def cancel_setting_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        try:
            await _show_settings(callback.message, callback.from_user.id)
        except BackendAPIError:
            await callback.answer("دریافت تنظیمات ممکن نشد.", show_alert=True)
            return
    await callback.answer("لغو شد.")
