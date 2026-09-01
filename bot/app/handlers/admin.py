import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin import (
    build_admin_back_keyboard,
    build_admin_home_keyboard,
)
from app.keyboards.payment import build_home_keyboard
from app.services.backend import (
    BackendAPIError,
    get_admin_context,
    list_application_settings,
    register_telegram_user,
)


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


@router.message(Command("admin"))
async def admin_command(message: Message) -> None:
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
async def open_admin_panel(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None:
        await callback.answer("دسترسی مدیریت ندارید.", show_alert=True)
        return

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
async def show_application_settings(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return

    context = await _context_or_none(callback.from_user.id)

    if context is None or not _can(context, "settings.view"):
        await callback.answer("دسترسی مشاهده تنظیمات ندارید.", show_alert=True)
        return

    try:
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


@router.callback_query(F.data == "admin:close")
async def close_admin_panel(callback: CallbackQuery) -> None:
    context = await _context_or_none(callback.from_user.id)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "پنل مدیریت بسته شد.",
            reply_markup=build_home_keyboard(
                include_admin=context is not None,
            ),
        )

    await callback.answer()
