from app.localization import tr as _tr, localized_collection as _localized_collection
import html

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app.handlers.payments import send_payment_offers_menu, send_subscription_status
from app.i18n import normalize_language
from app.keyboards.experience import (
    SUPPORT_CATEGORY_LABELS,
    build_custom_url_keyboard,
    build_required_membership_keyboard,
    build_support_admin_keyboard,
    build_support_categories_keyboard,
    build_support_ticket_detail_keyboard,
    build_support_ticket_list_keyboard,
    build_support_status_filters_keyboard,
    build_user_ticket_detail_keyboard,
    build_user_ticket_list_keyboard,
)
from app.keyboards.payment import build_home_reply_keyboard
from app.runtime_config import (
    runtime_configuration,
    runtime_content,
)
from app.services.backend import (
    BackendAPIError,
    close_support_ticket,
    create_support_ticket,
    get_admin_context,
    get_support_ticket,
    get_user_support_ticket,
    get_telegram_user,
    list_support_tickets,
    list_user_support_tickets,
    reply_support_ticket,
    user_reply_support_ticket,
    update_support_ticket_status,
    assign_support_ticket,
    reopen_support_ticket,
)
from app.state.experience import SupportStates
from app.services.topic_delivery import send_support_notification, send_attachment
from app.middleware.interface import ui_language


router = Router(name="experience")



def _can(context: dict, permission: str) -> bool:
    return bool(
        context.get("is_superadmin")
        or permission in set(context.get("permissions", []))
    )


async def _user_and_configuration(telegram_id: int) -> tuple[dict, dict]:
    try:
        user = await get_telegram_user(telegram_id)
        language = normalize_language(user.get("effective_language"))
    except Exception:
        user = {"telegram_id": telegram_id, "effective_language": "fa", "is_admin": False}
        language = "fa"
    return user, await runtime_configuration(language)


async def send_support_menu(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
) -> None:
    await state.clear()
    _user, configuration = await _user_and_configuration(telegram_id)
    language = normalize_language(configuration.get("language"))
    await message.answer(
        runtime_content(configuration, "support_intro"),
        reply_markup=build_support_categories_keyboard(language),
    )


async def send_content_page(
    message: Message,
    *,
    telegram_id: int,
    key: str,
) -> None:
    user, configuration = await _user_and_configuration(telegram_id)
    await message.answer(
        runtime_content(configuration, key),
        reply_markup=build_home_reply_keyboard(
            normalize_language(configuration.get("language")),
            include_admin=bool(user.get("is_admin")),
            configuration=configuration,
        ),
    )


async def perform_custom_button(
    message: Message,
    state: FSMContext,
    *,
    telegram_id: int,
    button: dict,
) -> None:
    action = str(button.get("action_type") or "")
    if action == "buy":
        await send_payment_offers_menu(message, state)
        return
    if action == "subscription":
        await send_subscription_status(message, telegram_id)
        return
    if action == "support":
        await send_support_menu(message, state, telegram_id=telegram_id)
        return
    if action in {"tutorial", "faq"}:
        await send_content_page(message, telegram_id=telegram_id, key=action)
        return
    if action == "message":
        user, configuration = await _user_and_configuration(telegram_id)
        language = normalize_language(configuration.get("language"))
        await message.answer(
            str(button.get("action_value") or ""),
            reply_markup=build_home_reply_keyboard(
                language,
                include_admin=bool(user.get("is_admin")),
                configuration=configuration,
            ),
        )
        return
    if action == "url" and button.get("action_value"):
        _user, configuration = await _user_and_configuration(telegram_id)
        language = normalize_language(configuration.get("language"))
        label = str(button.get(f"label_{language}") or button.get("label_fa") or _tr("بازکردن لینک"))
        await message.answer(
            label,
            reply_markup=build_custom_url_keyboard(
                label,
                str(button["action_value"]),
                str(button.get("style") or "default"),
            ),
        )


async def missing_required_channels(bot, telegram_id: int, configuration: dict) -> list[dict]:
    missing: list[dict] = []
    for channel in configuration.get("required_channels", []):
        chat_id = channel.get("chat_id")
        if not chat_id:
            continue
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=telegram_id)
            status = str(getattr(member, "status", ""))
            is_member = status in {"creator", "administrator", "member"} or bool(
                getattr(member, "is_member", False)
            )
        except Exception:
            is_member = False
        if not is_member:
            missing.append(channel)
    return missing


async def enforce_required_membership(
    message: Message,
    *,
    telegram_id: int,
    configuration: dict,
) -> bool:
    missing = await missing_required_channels(message.bot, telegram_id, configuration)
    if not missing:
        return True
    prompt_configuration = {**configuration, "required_channels": missing}
    await message.answer(
        runtime_content(configuration, "forced_join"),
        reply_markup=build_required_membership_keyboard(prompt_configuration),
    )
    return False


@router.callback_query(F.data == "support:open", F.message.chat.type == "private")
async def open_support(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await send_support_menu(
            callback.message,
            state,
            telegram_id=callback.from_user.id,
        )
    await callback.answer()


@router.callback_query(F.data.in_({"home:tutorial", "home:faq"}))
async def open_content_page(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message) and callback.data:
        await send_content_page(
            callback.message,
            telegram_id=callback.from_user.id,
            key=callback.data.rsplit(":", 1)[-1],
        )
    await callback.answer()


@router.callback_query(F.data.startswith("home:custom:"))
async def open_custom_button(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    try:
        button_id = int(callback.data.rsplit(":", 1)[-1])
        _user, configuration = await _user_and_configuration(callback.from_user.id)
        button = next(
            (
                item
                for item in configuration.get("custom_buttons", [])
                if int(item.get("id", 0)) == button_id
            ),
            None,
        )
        if button is None:
            await callback.answer("This button is no longer active." if normalize_language(configuration.get("language")) == "en" else _tr("این دکمه دیگر فعال نیست."), show_alert=True)
            return
        await perform_custom_button(
            callback.message,
            state,
            telegram_id=callback.from_user.id,
            button=button,
        )
        await callback.answer()
    except (TypeError, ValueError):
        await callback.answer(_tr("دکمه معتبر نیست."), show_alert=True)


@router.callback_query(F.data.startswith("support:category:"), F.message.chat.type == "private")
async def choose_support_category(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    category = callback.data.rsplit(":", 1)[-1]
    if category not in SUPPORT_CATEGORY_LABELS["fa"]:
        _user, configuration = await _user_and_configuration(callback.from_user.id)
        await callback.answer("Invalid subject." if normalize_language(configuration.get("language")) == "en" else _tr("موضوع معتبر نیست."), show_alert=True)
        return
    _user, configuration = await _user_and_configuration(callback.from_user.id)
    await state.set_state(SupportStates.waiting_for_user_message)
    await state.update_data(support_category=category)
    await callback.message.edit_text(
        runtime_content(configuration, "support_prompt"),
    )
    await callback.message.answer(
        "Send your support request:"
        if normalize_language(configuration.get("language")) == "en"
        else _tr("پیام پشتیبانی را ارسال کنید:"),
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.callback_query(F.data == "support:cancel")
async def cancel_support(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    language = "fa"
    if isinstance(callback.message, Message):
        _user, configuration = await _user_and_configuration(callback.from_user.id)
        language = normalize_language(configuration.get("language"))
        await callback.message.edit_text(
            "Support request cancelled." if language == "en" else _tr("درخواست پشتیبانی لغو شد.")
        )
    await callback.answer("Cancelled." if language == "en" else _tr("لغو شد."))


def _support_attachment(message: Message) -> tuple[str | None, str | None]:
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.document:
        return message.document.file_id, "document"
    if message.video:
        return message.video.file_id, "video"
    if message.voice:
        return message.voice.file_id, "voice"
    return None, None


def _ticket_notification(ticket: dict) -> str:
    user = ticket.get("user") or {}
    username = f"@{user['username']}" if user.get("username") else "—"
    full_name = " ".join(
        value for value in (user.get("first_name"), user.get("last_name")) if value
    ) or "—"
    category = SUPPORT_CATEGORY_LABELS["fa"].get(ticket.get("category"), _tr("پشتیبانی"))
    message = (ticket.get("messages") or [{}])[0]
    body = html.escape(str(message.get("body") or _tr("[پیوست]")))
    return (
        f"{_tr('🆕 <b>تیکت پشتیبانی #')}{ticket['id']}{_tr('</b>\n\nموضوع: <b>')}{html.escape(category)}{_tr('</b>\nنام: ')}{html.escape(full_name)}{_tr('\nنام کاربری: ')}{html.escape(username)}\nTelegram ID: <code>{user.get('telegram_id')}</code>\n\n{body}"
    )[:3900]


@router.message(SupportStates.waiting_for_user_message, F.chat.type == "private")
async def receive_support_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    category = str(data.get("support_category") or "")
    body = (message.text or message.caption or "").strip() or None
    file_id, file_type = _support_attachment(message)
    _user, configuration = await _user_and_configuration(message.from_user.id)
    language = normalize_language(configuration.get("language"))
    if body is None and file_id is None:
        await message.answer(
            "Please send text, an image, video, file, or voice message."
            if language == "en" else _tr("لطفاً متن، تصویر، ویدئو، فایل یا پیام صوتی ارسال کنید.")
        )
        return
    try:
        ticket = await create_support_ticket(
            telegram_id=message.from_user.id,
            category=category,
            body=body,
            telegram_file_id=file_id,
            file_type=file_type,
        )
        delivered = await send_support_notification(message.bot, ticket, initial=True)

        await state.clear()
        user, configuration = await _user_and_configuration(message.from_user.id)
        language = normalize_language(configuration.get("language"))
        suffix = "" if delivered else (
            "\n\n⚠️ Your ticket is saved. Immediate notification is unavailable; support can review it in the panel."
            if language == "en" else
            _tr("\n\n⚠️ مدیر فعالی برای دریافت فوری پیدا نشد؛ تیکت در پنل ذخیره شده است.")
        )
        tracking = f"Tracking ID: #{ticket['id']}" if language == "en" else f"{_tr('شناسه پیگیری: #')}{ticket['id']}"
        await message.answer(
            f"{runtime_content(configuration, 'support_sent')}\n{tracking}{suffix}",
            reply_markup=build_home_reply_keyboard(
                normalize_language(configuration.get("language")),
                include_admin=bool(user.get("is_admin")),
                configuration=configuration,
            ),
        )
    except BackendAPIError as exc:
        if exc.status_code == 409:
            await message.answer("You can have up to three open tickets. Reply to an existing ticket." if language == "en" else _tr("حداکثر سه تیکت باز مجاز است؛ به یکی از تیکت‌های موجود پاسخ بدهید."))
            return
        await message.answer(
            "❌ We could not submit your support request. Please try again later."
            if language == "en" else _tr("❌ ثبت درخواست پشتیبانی انجام نشد؛ کمی بعد دوباره تلاش کنید.")
        )


@router.callback_query(F.data.startswith("support:history:"), F.message.chat.type == "private")
async def user_ticket_history(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    try:
        page = max(1, int(callback.data.rsplit(":", 1)[-1]))
        _user, configuration = await _user_and_configuration(callback.from_user.id)
        language = normalize_language(configuration.get("language"))
        result = await list_user_support_tickets(callback.from_user.id, page=page)
        items = result.get("items") or []
        title = "🗂 <b>My tickets</b>" if language == "en" else _tr("🗂 <b>تاریخچه تیکت‌های من</b>")
        empty = "No tickets yet." if language == "en" else _tr("هنوز تیکتی ثبت نکرده‌اید.")
        await callback.message.edit_text(
            title + "\n\n" + (f"Total: <code>{int(result.get('total') or 0)}</code>" if language == "en" and items else f"{_tr('تعداد: <code>')}{int(result.get('total') or 0)}</code>" if items else empty),
            parse_mode="HTML",
            reply_markup=build_user_ticket_list_keyboard(
                items,
                page=page,
                total=int(result.get("total") or 0),
                language=language,
            ),
        )
        await callback.answer()
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer("Could not load tickets.", show_alert=True)


@router.callback_query(F.data.startswith("support:view:"), F.message.chat.type == "private")
async def user_ticket_detail(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    try:
        _, _, ticket_text, page_text = callback.data.split(":", 3)
        ticket_id = int(ticket_text)
        page = max(1, int(page_text))
        _user, configuration = await _user_and_configuration(callback.from_user.id)
        language = normalize_language(configuration.get("language"))
        ticket = await get_user_support_ticket(
            telegram_id=callback.from_user.id,
            ticket_id=ticket_id,
        )
        await callback.message.edit_text(
            _ticket_detail_text(ticket, language),
            parse_mode="HTML",
            reply_markup=build_user_ticket_detail_keyboard(
                ticket_id,
                is_closed=ticket.get("status") == "closed",
                page=page,
                language=language,
            ),
        )
        await callback.answer()
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer("Could not load the ticket.", show_alert=True)


@router.callback_query(F.data.startswith("support:reply:"), F.message.chat.type == "private")
async def begin_user_ticket_reply(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[-1])
        _user, configuration = await _user_and_configuration(callback.from_user.id)
        language = normalize_language(configuration.get("language"))
        await state.set_state(SupportStates.waiting_for_user_reply)
        await state.update_data(support_ticket_id=ticket_id)
        await callback.message.answer(
            f"Send your reply for ticket #{ticket_id}:" if language == "en" else f"{_tr('پاسخ جدید تیکت #')}{ticket_id}{_tr(' را بفرستید:')}",
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except (TypeError, ValueError):
        await callback.answer("Invalid ticket.", show_alert=True)


@router.message(SupportStates.waiting_for_user_reply, F.chat.type == "private")
async def receive_user_ticket_reply(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    body = (message.text or message.caption or "").strip() or None
    file_id, file_type = _support_attachment(message)
    try:
        ticket = await user_reply_support_ticket(
            telegram_id=message.from_user.id,
            ticket_id=int(data.get("support_ticket_id")),
            body=body,
            telegram_file_id=file_id,
            file_type=file_type,
        )
        await send_support_notification(message.bot, ticket)
        await state.clear()
        _user, configuration = await _user_and_configuration(message.from_user.id)
        language = normalize_language(configuration.get("language"))
        await message.answer(
            "✅ Your reply was added to the ticket." if language == "en" else _tr("✅ پاسخ شما به تیکت افزوده شد."),
            reply_markup=build_user_ticket_detail_keyboard(
                int(ticket["id"]), is_closed=False, page=1, language=language
            ),
        )
    except (BackendAPIError, TypeError, ValueError):
        _user, configuration = await _user_and_configuration(message.from_user.id)
        language = normalize_language(configuration.get("language"))
        await message.answer(
            "❌ Could not add your reply."
            if language == "en" else _tr("❌ افزودن پاسخ انجام نشد.")
        )


@router.callback_query(F.data == "membership:check")
async def check_membership(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return
    _user, configuration = await _user_and_configuration(callback.from_user.id)
    missing = await missing_required_channels(
        callback.message.bot,
        callback.from_user.id,
        configuration,
    )
    if missing:
        await callback.answer("Membership in all channels is not confirmed yet." if normalize_language(configuration.get("language")) == "en" else _tr("عضویت در همه کانال‌ها هنوز تأیید نشده است."), show_alert=True)
        return
    await callback.message.edit_text(runtime_content(configuration, "membership_verified"))
    await callback.answer("Membership confirmed." if normalize_language(configuration.get("language")) == "en" else _tr("عضویت تأیید شد."))


def _ticket_detail_text(ticket: dict, language: str = "fa") -> str:
    user = ticket.get("user") or {}
    is_fa = language != "en"
    category = SUPPORT_CATEGORY_LABELS["fa" if is_fa else "en"].get(
        ticket.get("category"), _tr("پشتیبانی") if is_fa else "Support"
    )
    statuses = {
        "new": (_tr("جدید"), "New"),
        "in_progress": (_tr("در حال بررسی"), "In progress"),
        "waiting_user": (_tr("منتظر کاربر"), "Waiting for you"),
        "answered": (_tr("پاسخ‌داده‌شده"), "Answered"),
        "closed": (_tr("بسته"), "Closed"),
    }
    status = statuses.get(ticket.get("status"), (_tr("نامشخص"), "Unknown"))[0 if is_fa else 1]
    lines = [
        f"🎫 <b>{(_tr('تیکت') if is_fa else 'Ticket')} #{ticket['id']}</b>",
        "",
        f"{(_tr('موضوع') if is_fa else 'Subject')}: <b>{html.escape(category)}</b>",
        f"{(_tr('وضعیت') if is_fa else 'Status')}: <b>{status}</b>",
        f"{(_tr('کاربر') if is_fa else 'User')}: <code>{user.get('telegram_id')}</code>",
        f"{(_tr('زبان') if is_fa else 'Language')}: <code>{user.get('effective_language') or 'fa'}</code>",
        f"{(_tr('پلن') if is_fa else 'Plan')}: <code>{html.escape(str(ticket.get('plan_name') or 'Free'))}</code>",
        "",
    ]
    for item in (ticket.get("messages") or [])[-1:]:
        sender = (
            (_tr("👤 کاربر") if is_fa else "👤 User")
            if item.get("sender_kind") == "user"
            else (_tr("👮 مدیر") if is_fa else "👮 Support")
        )
        body = html.escape(str(item.get("body") or f"[{item.get('file_type') or 'Attachment'}]")[:1200])
        lines.append(f"<b>{sender}:</b> {body}")
    return "\n".join(lines)


@router.callback_query(F.data == "admin:support")
async def admin_support_list(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        context = await get_admin_context(callback.from_user.id)
        if not context.get("is_admin") or not _can(context, "tickets.view"):
            await callback.answer(_tr("دسترسی مشاهده تیکت‌ها ندارید."), show_alert=True)
            return
        await state.clear()
        result = await list_support_tickets(callback.from_user.id, page=1)
        tickets = result.get("items") or []
        await callback.message.edit_text(
            _tr("🛟 <b>مدیریت تیکت‌های پشتیبانی</b>\n\n")
            + (f"{int(result.get('total') or 0)}{_tr(' تیکت وجود دارد.')}" if tickets else _tr("تیکتی وجود ندارد.")),
            parse_mode="HTML",
            reply_markup=build_support_ticket_list_keyboard(
                tickets, page=1, total=int(result.get("total") or 0)
            ),
        )
        await callback.answer()
    except BackendAPIError:
        await callback.answer(_tr("دریافت تیکت‌ها ممکن نشد."), show_alert=True)


@router.callback_query(F.data == "admin:support:filters")
async def admin_support_filters(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _tr("🔎 وضعیت موردنظر را انتخاب کنید:"),
            reply_markup=build_support_status_filters_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:support:list:"))
async def admin_support_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    try:
        _, _, _, status, page_text = callback.data.split(":", 4)
        page = max(1, int(page_text))
        result = await list_support_tickets(
            callback.from_user.id,
            status=None if status == "all" else status,
            page=page,
        )
        items = result.get("items") or []
        await state.clear()
        await callback.message.edit_text(
            f"{_tr('🛟 <b>تیکت\u200cهای پشتیبانی</b>\n\nتعداد: <code>')}{int(result.get('total') or 0)}</code>",
            parse_mode="HTML",
            reply_markup=build_support_ticket_list_keyboard(
                items,
                page=page,
                total=int(result.get("total") or 0),
                status=status,
            ),
        )
        await callback.answer()
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer(_tr("دریافت تیکت‌ها ممکن نشد."), show_alert=True)


@router.callback_query(F.data.startswith("admin:support:ticket:"))
async def admin_support_detail(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    try:
        parts = callback.data.split(":")
        ticket_id = int(parts[3])
        ticket = await get_support_ticket(
            actor_telegram_id=callback.from_user.id,
            ticket_id=ticket_id,
        )
        await callback.message.edit_text(
            _ticket_detail_text(ticket, ui_language.get()),
            parse_mode="HTML",
            reply_markup=build_support_ticket_detail_keyboard(
                ticket_id,
                is_closed=ticket.get("status") == "closed",
            ),
        )
        await callback.answer()
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer(_tr("دریافت تیکت ممکن نشد."), show_alert=True)


@router.callback_query(F.data.startswith("support_admin:reply:"))
async def begin_admin_support_reply(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    context = await get_admin_context(callback.from_user.id)
    if not context.get("is_admin") or not _can(context, "tickets.reply"):
        await callback.answer(_tr("دسترسی پاسخ‌گویی ندارید."), show_alert=True)
        return
    ticket_id = int(callback.data.rsplit(":", 1)[-1])
    await state.set_state(SupportStates.waiting_for_admin_reply)
    await state.update_data(support_ticket_id=ticket_id)
    await callback.message.answer(
        f"{_tr('پاسخ تیکت #')}{ticket_id}{_tr(' را ارسال کنید:')}",
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.message(SupportStates.waiting_for_admin_reply, F.text)
async def receive_admin_support_reply(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text:
        return
    data = await state.get_data()
    try:
        ticket_id = int(data.get("support_ticket_id"))
        ticket = await reply_support_ticket(
            actor_telegram_id=message.from_user.id,
            ticket_id=ticket_id,
            body=message.text,
        )
    except (BackendAPIError, TypeError, ValueError):
        await message.answer(_tr("❌ ثبت یا ارسال پاسخ انجام نشد."))
        return
    # The database write succeeded. A delivery failure must not ask the admin
    # to submit the same reply again.
    await state.clear()
    try:
        user = ticket.get("user") or {}
        configuration = await runtime_configuration(
            normalize_language(user.get("effective_language"))
        )
        language = normalize_language(configuration.get("language"))
        await message.bot.send_message(
            chat_id=int(user["telegram_id"]),
            text=(
                f"🛟 <b>Support reply — ticket #{ticket_id}</b>\n\n"
                if language == "en"
                else f"🛟 <b>پاسخ پشتیبانی — تیکت #{ticket_id}</b>\n\n"
            ) + html.escape(message.text),
            parse_mode="HTML",
            reply_markup=build_home_reply_keyboard(
                normalize_language(configuration.get("language")),
                include_admin=False,
                configuration=configuration,
            ),
        )
        await message.answer(_tr("✅ پاسخ ثبت و برای کاربر ارسال شد."))
    except (TelegramAPIError, BackendAPIError, TypeError, ValueError, KeyError):
        from app.middleware.interface import ui_language
        await message.answer("Reply saved in the ticket history, but delivery to the user failed." if ui_language.get() == "en" else
            "پاسخ در تاریخچهٔ تیکت ذخیره شد، اما ارسال پیام به کاربر انجام نشد.")


@router.callback_query(F.data.startswith("support_admin:close:"))
async def close_admin_support(callback: CallbackQuery) -> None:
    if not callback.data:
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[-1])
        await close_support_ticket(
            actor_telegram_id=callback.from_user.id,
            ticket_id=ticket_id,
        )
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer(_tr("تیکت بسته شد."))
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer(_tr("بستن تیکت انجام نشد."), show_alert=True)


@router.callback_query(F.data.startswith("support_admin:status:"))
async def set_admin_support_status(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        _, _, status, ticket_text = callback.data.split(":", 3)
        ticket_id = int(ticket_text)
        context = await get_admin_context(callback.from_user.id)
        if not _can(context, "tickets.manage"):
            await callback.answer(_tr("دسترسی مدیریت تیکت ندارید."), show_alert=True)
            return
        ticket = await update_support_ticket_status(
            actor_telegram_id=callback.from_user.id,
            ticket_id=ticket_id,
            status=status,
        )
        await callback.message.edit_text(
            _ticket_detail_text(ticket, ui_language.get()),
            parse_mode="HTML",
            reply_markup=build_support_ticket_detail_keyboard(
                ticket_id, is_closed=ticket.get("status") == "closed"
            ),
        )
        await callback.answer(_tr("وضعیت تیکت تغییر کرد."))
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer(_tr("تغییر وضعیت انجام نشد."), show_alert=True)


@router.callback_query(F.data.startswith("support_admin:assign:"))
async def begin_support_assignment(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[-1])
        context = await get_admin_context(callback.from_user.id)
        if not _can(context, "tickets.manage"):
            await callback.answer(_tr("دسترسی ارجاع تیکت ندارید."), show_alert=True)
            return
        await state.set_state(SupportStates.waiting_for_assignment)
        await state.update_data(support_ticket_id=ticket_id)
        await callback.message.answer(
            _tr("Telegram ID مدیر مقصد را بفرستید:"),
            reply_markup=ForceReply(selective=True),
        )
        await callback.answer()
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer(_tr("ارجاع تیکت ممکن نشد."), show_alert=True)


@router.message(SupportStates.waiting_for_assignment, F.text)
async def receive_support_assignment(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not message.text:
        return
    try:
        assignee_id = int(message.text.strip())
        data = await state.get_data()
        ticket_id = int(data.get("support_ticket_id"))
        await assign_support_ticket(
            actor_telegram_id=message.from_user.id,
            ticket_id=ticket_id,
            assignee_telegram_id=assignee_id,
        )
        await state.clear()
        await message.answer(f"{_tr('✅ تیکت #')}{ticket_id}{_tr(' به مدیر ')}{assignee_id}{_tr(' ارجاع شد.')}")
    except (BackendAPIError, TypeError, ValueError):
        await message.answer(_tr("❌ مدیر مقصد معتبر نیست یا دسترسی تیکت ندارد."))


@router.callback_query(F.data.startswith("support_admin:reopen:"))
async def reopen_admin_support(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[-1])
        context = await get_admin_context(callback.from_user.id)
        if not _can(context, "tickets.manage"):
            await callback.answer(_tr("دسترسی بازگشایی تیکت ندارید."), show_alert=True)
            return
        ticket = await reopen_support_ticket(
            actor_telegram_id=callback.from_user.id,
            ticket_id=ticket_id,
        )
        await callback.message.edit_text(
            _ticket_detail_text(ticket, ui_language.get()),
            parse_mode="HTML",
            reply_markup=build_support_ticket_detail_keyboard(ticket_id, is_closed=False),
        )
        await callback.answer(_tr("تیکت بازگشایی شد."))
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer(_tr("بازگشایی تیکت انجام نشد."), show_alert=True)


@router.callback_query(F.data.startswith("ticketlog:"))
async def ticket_message_history(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    try:
        _, audience, ticket_id, page_text = callback.data.split(":")
        ticket_id, page = int(ticket_id), max(1, int(page_text))
        if audience == "u":
            if callback.message.chat.type != "private":
                return await callback.answer("Open your ticket in the private chat.", show_alert=True)
            ticket = await get_user_support_ticket(telegram_id=callback.from_user.id, ticket_id=ticket_id)
            back = f"support:view:{ticket_id}:1"
        elif audience == "a":
            ticket = await get_support_ticket(actor_telegram_id=callback.from_user.id, ticket_id=ticket_id)
            back = f"admin:support:ticket:{ticket_id}"
        else:
            raise ValueError("audience")
        messages = ticket.get("messages") or []
        total = max(1, len(messages))
        page = min(page, total)
        item = messages[page - 1] if messages else {}
        body = str(item.get("body") or "[Attachment]")
        # Escape each complete chunk, never truncate HTML or hide old messages.
        text = f"🎫 #{ticket_id} · {page}/{total}\n{item.get('created_at', '')} · {item.get('sender_kind', '')}\n\n"
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ticketlog:{audience}:{ticket_id}:{page - 1}"))
        if page < total:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ticketlog:{audience}:{ticket_id}:{page + 1}"))
        rows = [nav] if nav else []
        rows.append([InlineKeyboardButton(text="↩️", callback_data=back)])
        await callback.message.edit_text(html.escape(text + body[:2800]), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        for offset in range(2800, len(body), 2800):
            await callback.message.answer(body[offset:offset + 2800], parse_mode=None)
        await send_attachment(callback.bot, item, chat_id=callback.message.chat.id,
                              message_thread_id=callback.message.message_thread_id)
        await callback.answer()
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer("Could not load ticket history.", show_alert=True)
