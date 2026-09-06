import html
import re
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.keyboards.admin_experience import (
    BUTTON_LABELS,
    CONTENT_LABELS,
    build_channel_delete_keyboard,
    build_channel_detail_keyboard,
    build_channels_admin_keyboard,
    build_copy_cancel_keyboard,
    build_copy_items_keyboard,
    build_copy_root_keyboard,
    build_copy_section_keyboard,
    build_home_action_keyboard,
    build_home_button_delete_keyboard,
    build_home_button_detail_keyboard,
    build_home_buttons_admin_keyboard,
    build_home_style_keyboard,
)
from app.runtime_config import clear_runtime_configuration_cache
from app.services.backend import (
    BackendAPIError,
    create_home_button,
    create_required_channel,
    delete_home_button,
    delete_required_channel,
    get_admin_context,
    list_application_settings,
    list_home_buttons,
    list_required_channels,
    update_application_setting,
    update_home_button,
    update_required_channel,
)
from app.state.admin_experience import AdminExperienceStates


router = Router(name="admin-experience")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

LANGUAGES = {"fa", "en"}
SECTIONS = {"content", "buttons"}
HOME_ACTIONS = {
    "url",
    "message",
    "buy",
    "subscription",
    "support",
    "tutorial",
    "faq",
}
HOME_STYLES = {"default", "primary", "success", "danger"}
CHANNEL_ID_PATTERN = re.compile(r"(?:@[A-Za-z0-9_]{5,32}|-100[0-9]{5,20})")


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


def _api_error(exc: BackendAPIError) -> str:
    if exc.status_code == 403:
        return "دسترسی لازم برای این عملیات را ندارید."
    if exc.status_code == 404:
        return "رکورد موردنظر پیدا نشد؛ فهرست را دوباره باز کنید."
    if exc.status_code == 409:
        return "این مورد هم‌زمان تغییر کرده یا تکراری است؛ فهرست را تازه کنید."
    if exc.status_code == 422:
        return "اطلاعات واردشده معتبر نیست."
    return "ارتباط با تنظیمات ربات انجام نشد؛ کمی بعد دوباره تلاش کنید."


def _copy_setting_key(language: str, section: str) -> str:
    return f"bot.{section}.{language}"


def _copy_labels(section: str) -> dict[str, str]:
    return CONTENT_LABELS if section == "content" else BUTTON_LABELS


async def _setting_row(
    actor_telegram_id: int,
    *,
    language: str,
    section: str,
) -> dict | None:
    key = _copy_setting_key(language, section)
    rows = await list_application_settings(actor_telegram_id)
    return next((row for row in rows if row.get("key") == key), None)


async def _show_home_buttons(message: Message, actor_telegram_id: int) -> None:
    buttons = await list_home_buttons(actor_telegram_id)
    await message.edit_text(
        "🧩 <b>دکمه‌های سفارشی صفحه اصلی</b>\n\n"
        "دکمه‌های فعال در منوی کاربران نمایش داده می‌شوند. "
        "عملکرد هر دکمه می‌تواند لینک، متن یا یکی از بخش‌های ربات باشد.",
        parse_mode="HTML",
        reply_markup=build_home_buttons_admin_keyboard(buttons),
    )


async def _find_home_button(actor_telegram_id: int, button_id: int) -> dict | None:
    return next(
        (
            item
            for item in await list_home_buttons(actor_telegram_id)
            if int(item.get("id", 0)) == button_id
        ),
        None,
    )


def _home_button_text(button: dict) -> str:
    actions = {
        "url": "بازکردن لینک",
        "message": "نمایش متن",
        "buy": "خرید اشتراک",
        "subscription": "وضعیت اشتراک",
        "support": "پشتیبانی",
        "tutorial": "آموزش",
        "faq": "سوالات متداول",
    }
    styles = {
        "default": "معمولی",
        "primary": "آبی",
        "success": "سبز",
        "danger": "قرمز",
    }
    value = str(button.get("action_value") or "—")
    return (
        "🧩 <b>مشخصات دکمه سفارشی</b>\n\n"
        f"عنوان فارسی: <b>{html.escape(str(button.get('label_fa') or '—'))}</b>\n"
        f"عنوان انگلیسی: <b>{html.escape(str(button.get('label_en') or '—'))}</b>\n"
        f"عملکرد: <b>{actions.get(str(button.get('action_type')), 'نامشخص')}</b>\n"
        f"مقدار: <code>{html.escape(value[:500])}</code>\n"
        f"رنگ: <b>{styles.get(str(button.get('style')), 'معمولی')}</b>\n"
        f"وضعیت: <b>{'فعال ✅' if button.get('is_active') else 'غیرفعال ⛔️'}</b>"
    )


async def _show_channels(message: Message, actor_telegram_id: int) -> None:
    channels = await list_required_channels(actor_telegram_id)
    await message.edit_text(
        "📢 <b>عضویت اجباری کانال‌ها</b>\n\n"
        "برای بررسی مطمئن عضویت کاربران، ربات باید در هر کانال مدیر باشد. "
        "این محدودیت فقط برای پلن‌هایی اعمال می‌شود که عضویت اجباری‌شان فعال است.",
        parse_mode="HTML",
        reply_markup=build_channels_admin_keyboard(channels),
    )


async def _find_channel(actor_telegram_id: int, channel_id: int) -> dict | None:
    return next(
        (
            item
            for item in await list_required_channels(actor_telegram_id)
            if int(item.get("id", 0)) == channel_id
        ),
        None,
    )


def _channel_text(channel: dict) -> str:
    return (
        "📢 <b>مشخصات کانال اجباری</b>\n\n"
        f"عنوان: <b>{html.escape(str(channel.get('title') or '—'))}</b>\n"
        f"شناسه: <code>{html.escape(str(channel.get('chat_id') or '—'))}</code>\n"
        f"لینک عضویت: <code>{html.escape(str(channel.get('invite_url') or '—'))}</code>\n"
        f"وضعیت: <b>{'فعال ✅' if channel.get('is_active') else 'غیرفعال ⛔️'}</b>"
    )


@router.callback_query(F.data == "admin:copy")
async def copy_root(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ویرایش متن‌ها را ندارید.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "📝 <b>متن‌ها و عنوان دکمه‌ها</b>\n\nزبان موردنظر را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=build_copy_root_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:copy:lang:(fa|en)$"))
async def copy_language(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    language = callback.data.rsplit(":", 1)[-1]
    await state.clear()
    await callback.message.edit_text(
        f"📝 <b>ویرایش {'فارسی' if language == 'fa' else 'English'}</b>\n\nبخش موردنظر را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=build_copy_section_keyboard(language),
    )
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"^admin:copy:section:(fa|en):(content|buttons)$")
)
async def copy_section(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    _admin, _copy, _section, language, section = callback.data.split(":")
    await state.clear()
    await callback.message.edit_text(
        (
            "📝 <b>متن محتواها</b>\n\nیک مورد را برای ویرایش انتخاب کنید:"
            if section == "content"
            else "🔘 <b>عنوان دکمه‌ها</b>\n\nیک دکمه را برای ویرایش انتخاب کنید:"
        ),
        parse_mode="HTML",
        reply_markup=build_copy_items_keyboard(language, section),
    )
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"^admin:copy:item:(fa|en):(content|buttons):[a-z_]+$")
)
async def begin_copy_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    _admin, _copy, _item, language, section, item_key = callback.data.split(":")
    labels = _copy_labels(section)
    if language not in LANGUAGES or item_key not in labels:
        await callback.answer("گزینه معتبر نیست.", show_alert=True)
        return
    try:
        row = await _setting_row(
            callback.from_user.id,
            language=language,
            section=section,
        )
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)
        return
    if row is None:
        await callback.answer("تنظیم مربوط در دیتابیس پیدا نشد.", show_alert=True)
        return
    values = row.get("value") if isinstance(row.get("value"), dict) else {}
    current = str(values.get(item_key) or "—")
    await state.clear()
    await state.set_state(AdminExperienceStates.waiting_for_copy_value)
    await state.update_data(
        copy_language=language,
        copy_section=section,
        copy_item_key=item_key,
        copy_setting_row=row,
    )
    await callback.message.edit_text(
        f"✏️ <b>{html.escape(labels[item_key])}</b>\n\n"
        f"مقدار فعلی:\n<code>{html.escape(current[:3000])}</code>\n\n"
        "مقدار جدید را در یک پیام بفرستید.",
        parse_mode="HTML",
        reply_markup=build_copy_cancel_keyboard(language, section),
    )
    await callback.message.answer(
        "متن جدید را ارسال کنید:",
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.message(AdminExperienceStates.waiting_for_copy_value)
async def save_copy_value(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text:
        await message.answer("مقدار باید به‌صورت متن ارسال شود.")
        return
    if await _context(message.from_user.id, "settings.manage") is None:
        await state.clear()
        return
    data = await state.get_data()
    language = str(data.get("copy_language") or "")
    section = str(data.get("copy_section") or "")
    item_key = str(data.get("copy_item_key") or "")
    row = data.get("copy_setting_row")
    value = message.text.strip()
    maximum = 3900 if section == "content" else 64
    if not value or len(value) > maximum:
        await message.answer(f"متن باید بین ۱ تا {maximum} نویسه باشد.")
        return
    if (
        language not in LANGUAGES
        or section not in SECTIONS
        or item_key not in _copy_labels(section)
        or not isinstance(row, dict)
    ):
        await state.clear()
        await message.answer("درخواست ویرایش منقضی شده است؛ دوباره از پنل وارد شوید.")
        return
    updated_values = dict(row.get("value") or {})
    updated_values[item_key] = value
    try:
        await update_application_setting(
            actor_telegram_id=message.from_user.id,
            key=str(row["key"]),
            category=str(row.get("category") or "bot"),
            value=updated_values,
            expected_version=int(row["version"]),
            description=row.get("description"),
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await message.answer(
            "✅ متن با موفقیت ذخیره شد.",
            reply_markup=build_copy_items_keyboard(language, section),
        )
    except BackendAPIError as exc:
        await message.answer(f"❌ {_api_error(exc)}")


@router.callback_query(F.data == "admin:homebuttons")
async def home_buttons(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی مدیریت دکمه‌ها را ندارید.", show_alert=True)
        return
    await state.clear()
    try:
        await _show_home_buttons(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)


@router.callback_query(F.data == "admin:homebutton:add")
async def add_home_button(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminExperienceStates.waiting_for_home_label_fa)
    await callback.message.edit_text(
        "➕ <b>افزودن دکمه سفارشی</b>\n\nعنوان فارسی دکمه را بفرستید (حداکثر ۶۴ نویسه).",
        parse_mode="HTML",
        reply_markup=build_home_buttons_admin_keyboard([]),
    )
    await callback.message.answer("عنوان فارسی:", reply_markup=ForceReply(selective=True))
    await callback.answer()


@router.message(AdminExperienceStates.waiting_for_home_label_fa)
async def home_label_fa(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 1 <= len(value) <= 64:
        await message.answer("عنوان فارسی باید بین ۱ تا ۶۴ نویسه باشد.")
        return
    await state.update_data(home_label_fa=value)
    await state.set_state(AdminExperienceStates.waiting_for_home_label_en)
    await message.answer(
        "عنوان انگلیسی دکمه را بفرستید (حداکثر ۶۴ نویسه):",
        reply_markup=ForceReply(selective=True),
    )


@router.message(AdminExperienceStates.waiting_for_home_label_en)
async def home_label_en(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 1 <= len(value) <= 64:
        await message.answer("عنوان انگلیسی باید بین ۱ تا ۶۴ نویسه باشد.")
        return
    await state.update_data(home_label_en=value, home_button_id="new")
    await state.set_state(AdminExperienceStates.selecting_home_action)
    await message.answer(
        "عملکرد دکمه را انتخاب کنید:",
        reply_markup=build_home_action_keyboard(),
    )


@router.callback_query(F.data.regexp(r"^admin:hbaction:(new|[0-9]+):[a-z]+$"))
async def choose_home_action(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    _admin, _kind, raw_id, action = callback.data.split(":")
    if action not in HOME_ACTIONS:
        await callback.answer("عملکرد معتبر نیست.", show_alert=True)
        return
    await state.update_data(home_button_id=raw_id, home_action_type=action)
    if action in {"url", "message"}:
        await state.set_state(AdminExperienceStates.waiting_for_home_action_value)
        instruction = (
            "لینک HTTPS کامل را بفرستید:"
            if action == "url"
            else "متنی را که پس از زدن دکمه نمایش داده شود بفرستید:"
        )
        await callback.message.answer(instruction, reply_markup=ForceReply(selective=True))
        await callback.answer()
        return
    if raw_id == "new":
        await state.set_state(AdminExperienceStates.selecting_home_style)
        await callback.message.edit_text(
            "رنگ دکمه را انتخاب کنید:",
            reply_markup=build_home_style_keyboard(),
        )
        await callback.answer()
        return
    try:
        button = await update_home_button(
            actor_telegram_id=callback.from_user.id,
            button_id=int(raw_id),
            changes={"action_type": action, "action_value": None},
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await callback.message.edit_text(
            _home_button_text(button),
            parse_mode="HTML",
            reply_markup=build_home_button_detail_keyboard(button),
        )
        await callback.answer("عملکرد دکمه تغییر کرد.")
    except (BackendAPIError, ValueError) as exc:
        text = _api_error(exc) if isinstance(exc, BackendAPIError) else "شناسه دکمه معتبر نیست."
        await callback.answer(text, show_alert=True)


@router.message(AdminExperienceStates.waiting_for_home_action_value)
async def home_action_value(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text:
        await message.answer("مقدار باید به‌صورت متن ارسال شود.")
        return
    data = await state.get_data()
    raw_id = str(data.get("home_button_id") or "")
    action = str(data.get("home_action_type") or "")
    value = message.text.strip()
    if action == "url" and not re.fullmatch(r"https://\S+", value):
        await message.answer("لینک باید کامل باشد و با https:// شروع شود.")
        return
    if action == "message" and not 1 <= len(value) <= 3900:
        await message.answer("متن باید بین ۱ تا ۳۹۰۰ نویسه باشد.")
        return
    await state.update_data(home_action_value=value)
    if raw_id == "new":
        await state.set_state(AdminExperienceStates.selecting_home_style)
        await message.answer(
            "رنگ دکمه را انتخاب کنید:",
            reply_markup=build_home_style_keyboard(),
        )
        return
    try:
        button = await update_home_button(
            actor_telegram_id=message.from_user.id,
            button_id=int(raw_id),
            changes={"action_type": action, "action_value": value},
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await message.answer(
            "✅ عملکرد دکمه تغییر کرد.\n\n" + _home_button_text(button),
            parse_mode="HTML",
            reply_markup=build_home_button_detail_keyboard(button),
        )
    except (BackendAPIError, ValueError) as exc:
        text = _api_error(exc) if isinstance(exc, BackendAPIError) else "درخواست معتبر نیست."
        await message.answer(f"❌ {text}")


@router.callback_query(F.data.regexp(r"^admin:hbstyle:(new|[0-9]+):(default|primary|success|danger)$"))
async def choose_home_style(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    _admin, _kind, raw_id, style = callback.data.split(":")
    if style not in HOME_STYLES:
        await callback.answer("رنگ معتبر نیست.", show_alert=True)
        return
    try:
        if raw_id == "new":
            data = await state.get_data()
            action = str(data.get("home_action_type") or "")
            button = await create_home_button(
                actor_telegram_id=callback.from_user.id,
                data={
                    "label_fa": str(data.get("home_label_fa") or ""),
                    "label_en": str(data.get("home_label_en") or ""),
                    "action_type": action,
                    "action_value": data.get("home_action_value"),
                    "style": style,
                    "sort_order": 100,
                    "is_active": True,
                },
            )
        else:
            button = await update_home_button(
                actor_telegram_id=callback.from_user.id,
                button_id=int(raw_id),
                changes={"style": style},
            )
        clear_runtime_configuration_cache()
        await state.clear()
        await callback.message.edit_text(
            _home_button_text(button),
            parse_mode="HTML",
            reply_markup=build_home_button_detail_keyboard(button),
        )
        await callback.answer("دکمه ذخیره شد.")
    except (BackendAPIError, ValueError) as exc:
        text = _api_error(exc) if isinstance(exc, BackendAPIError) else "اطلاعات دکمه کامل نیست."
        await callback.answer(text, show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:homebutton:edit:(fa|en):[0-9]+$"))
async def begin_home_label_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "settings.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    _admin, _homebutton, _edit, language, raw_id = callback.data.split(":")
    await state.clear()
    await state.set_state(AdminExperienceStates.waiting_for_home_edit_value)
    await state.update_data(home_edit_id=int(raw_id), home_edit_language=language)
    await callback.message.answer(
        f"عنوان جدید {'فارسی' if language == 'fa' else 'انگلیسی'} را بفرستید:",
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.message(AdminExperienceStates.waiting_for_home_edit_value)
async def save_home_label_edit(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    value = str(message.text or "").strip()
    if not 1 <= len(value) <= 64:
        await message.answer("عنوان باید بین ۱ تا ۶۴ نویسه باشد.")
        return
    data = await state.get_data()
    language = str(data.get("home_edit_language") or "")
    try:
        button = await update_home_button(
            actor_telegram_id=message.from_user.id,
            button_id=int(data.get("home_edit_id")),
            changes={f"label_{language}": value},
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await message.answer(
            "✅ عنوان دکمه تغییر کرد.\n\n" + _home_button_text(button),
            parse_mode="HTML",
            reply_markup=build_home_button_detail_keyboard(button),
        )
    except (BackendAPIError, TypeError, ValueError) as exc:
        text = _api_error(exc) if isinstance(exc, BackendAPIError) else "درخواست معتبر نیست."
        await message.answer(f"❌ {text}")


@router.callback_query(F.data.regexp(r"^admin:homebutton:action:[0-9]+$"))
async def edit_home_action(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    button_id = int(callback.data.rsplit(":", 1)[-1])
    await state.clear()
    await state.update_data(home_button_id=str(button_id))
    await state.set_state(AdminExperienceStates.selecting_home_action)
    await callback.message.edit_text(
        "عملکرد جدید را انتخاب کنید:",
        reply_markup=build_home_action_keyboard(button_id=button_id),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:homebutton:style:[0-9]+$"))
async def edit_home_style(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    button_id = int(callback.data.rsplit(":", 1)[-1])
    await state.clear()
    await state.set_state(AdminExperienceStates.selecting_home_style)
    await callback.message.edit_text(
        "رنگ جدید را انتخاب کنید:",
        reply_markup=build_home_style_keyboard(button_id=button_id),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:homebutton:toggle:[0-9]+$"))
async def toggle_home_button(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        button_id = int(callback.data.rsplit(":", 1)[-1])
        current = await _find_home_button(callback.from_user.id, button_id)
        if current is None:
            raise ValueError
        button = await update_home_button(
            actor_telegram_id=callback.from_user.id,
            button_id=button_id,
            changes={"is_active": not bool(current.get("is_active"))},
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await callback.message.edit_text(
            _home_button_text(button),
            parse_mode="HTML",
            reply_markup=build_home_button_detail_keyboard(button),
        )
        await callback.answer("وضعیت دکمه تغییر کرد.")
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)
    except ValueError:
        await callback.answer("دکمه پیدا نشد.", show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:homebutton:deleteask:[0-9]+$"))
async def ask_delete_home_button(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    button_id = int(callback.data.rsplit(":", 1)[-1])
    await callback.message.edit_text(
        "⚠️ این دکمه برای همه کاربران حذف شود؟",
        reply_markup=build_home_button_delete_keyboard(button_id),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:homebutton:delete:[0-9]+$"))
async def confirm_delete_home_button(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        await delete_home_button(
            actor_telegram_id=callback.from_user.id,
            button_id=int(callback.data.rsplit(":", 1)[-1]),
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await _show_home_buttons(callback.message, callback.from_user.id)
        await callback.answer("دکمه حذف شد.")
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:homebutton:[0-9]+$"))
async def home_button_detail(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        button = await _find_home_button(
            callback.from_user.id,
            int(callback.data.rsplit(":", 1)[-1]),
        )
        if button is None:
            raise ValueError
        await state.clear()
        await callback.message.edit_text(
            _home_button_text(button),
            parse_mode="HTML",
            reply_markup=build_home_button_detail_keyboard(button),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)
    except ValueError:
        await callback.answer("دکمه پیدا نشد.", show_alert=True)


@router.callback_query(F.data == "admin:channels")
async def channels(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "forced_join.manage") is None:
        await callback.answer("دسترسی مدیریت عضویت اجباری را ندارید.", show_alert=True)
        return
    await state.clear()
    try:
        await _show_channels(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)


@router.callback_query(F.data == "admin:channel:add")
async def add_channel(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "forced_join.manage") is None:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminExperienceStates.waiting_for_channel_chat_id)
    await callback.message.edit_text(
        "➕ <b>افزودن کانال اجباری</b>\n\n"
        "ابتدا ربات را در کانال مدیر کنید؛ سپس شناسه عمومی مانند "
        "<code>@channelname</code> یا شناسه عددی <code>-100...</code> را بفرستید.",
        parse_mode="HTML",
        reply_markup=build_channels_admin_keyboard([]),
    )
    await callback.message.answer("شناسه کانال:", reply_markup=ForceReply(selective=True))
    await callback.answer()


@router.message(AdminExperienceStates.waiting_for_channel_chat_id)
async def channel_chat_id(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if CHANNEL_ID_PATTERN.fullmatch(value) is None:
        await message.answer("شناسه باید @username یا شناسه عددی کامل -100... باشد.")
        return
    try:
        chat = await message.bot.get_chat(value)
        bot_user = await message.bot.get_me()
        member = await message.bot.get_chat_member(value, bot_user.id)
        status = getattr(getattr(member, "status", None), "value", getattr(member, "status", ""))
        if str(status) not in {"administrator", "creator"}:
            await message.answer("ابتدا ربات را در این کانال مدیر کنید و دوباره شناسه را بفرستید.")
            return
        canonical = str(getattr(chat, "id", value)) if value.startswith("-100") else value
    except Exception:
        await message.answer(
            "ربات به این کانال دسترسی ندارد. مدیر بودن ربات و صحیح بودن شناسه را بررسی کنید."
        )
        return
    await state.update_data(channel_chat_id=canonical)
    await state.set_state(AdminExperienceStates.waiting_for_channel_title)
    await message.answer("عنوانی که کاربر ببیند را بفرستید:", reply_markup=ForceReply(selective=True))


@router.message(AdminExperienceStates.waiting_for_channel_title)
async def channel_title(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 1 <= len(value) <= 120:
        await message.answer("عنوان باید بین ۱ تا ۱۲۰ نویسه باشد.")
        return
    await state.update_data(channel_title=value)
    await state.set_state(AdminExperienceStates.waiting_for_channel_invite_url)
    await message.answer(
        "لینک عضویت کانال را با https://t.me/ بفرستید:",
        reply_markup=ForceReply(selective=True),
    )


@router.message(AdminExperienceStates.waiting_for_channel_invite_url)
async def channel_invite_url(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    value = str(message.text or "").strip()
    if not re.fullmatch(r"https://(?:www\.)?(?:t\.me|telegram\.me)/\S+", value):
        await message.answer("لینک معتبر تلگرام باید با https://t.me/ شروع شود.")
        return
    data = await state.get_data()
    try:
        channel = await create_required_channel(
            actor_telegram_id=message.from_user.id,
            data={
                "chat_id": str(data.get("channel_chat_id") or ""),
                "title": str(data.get("channel_title") or ""),
                "invite_url": value,
                "sort_order": 0,
                "is_active": True,
            },
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await message.answer(
            "✅ کانال ذخیره شد.\n\n" + _channel_text(channel),
            parse_mode="HTML",
            reply_markup=build_channel_detail_keyboard(channel),
        )
    except BackendAPIError as exc:
        await message.answer(f"❌ {_api_error(exc)}")


@router.callback_query(F.data.regexp(r"^admin:channel:toggle:[0-9]+$"))
async def toggle_channel(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        channel_id = int(callback.data.rsplit(":", 1)[-1])
        current = await _find_channel(callback.from_user.id, channel_id)
        if current is None:
            raise ValueError
        channel = await update_required_channel(
            actor_telegram_id=callback.from_user.id,
            channel_id=channel_id,
            changes={"is_active": not bool(current.get("is_active"))},
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await callback.message.edit_text(
            _channel_text(channel),
            parse_mode="HTML",
            reply_markup=build_channel_detail_keyboard(channel),
        )
        await callback.answer("وضعیت کانال تغییر کرد.")
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)
    except ValueError:
        await callback.answer("کانال پیدا نشد.", show_alert=True)

@router.callback_query(F.data.regexp(r"^admin:channel:deleteask:[0-9]+$"))
async def ask_delete_channel(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    channel_id = int(callback.data.rsplit(":", 1)[-1])
    await callback.message.edit_text(
        "⚠️ این کانال از عضویت اجباری حذف شود؟",
        reply_markup=build_channel_delete_keyboard(channel_id),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^admin:channel:delete:[0-9]+$"))
async def confirm_delete_channel(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        await delete_required_channel(
            actor_telegram_id=callback.from_user.id,
            channel_id=int(callback.data.rsplit(":", 1)[-1]),
        )
        clear_runtime_configuration_cache()
        await state.clear()
        await _show_channels(callback.message, callback.from_user.id)
        await callback.answer("کانال حذف شد.")
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:channel:[0-9]+$"))
async def channel_detail(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        channel = await _find_channel(
            callback.from_user.id,
            int(callback.data.rsplit(":", 1)[-1]),
        )
        if channel is None:
            raise ValueError
        await state.clear()
        await callback.message.edit_text(
            _channel_text(channel),
            parse_mode="HTML",
            reply_markup=build_channel_detail_keyboard(channel),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_api_error(exc), show_alert=True)
    except ValueError:
        await callback.answer("کانال پیدا نشد.", show_alert=True)
