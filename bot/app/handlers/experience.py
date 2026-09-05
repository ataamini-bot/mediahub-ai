import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

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
    get_telegram_user,
    list_support_tickets,
    reply_support_ticket,
)
from app.state.experience import SupportStates


router = Router(name="experience")
router.message.filter(F.chat.type == "private")


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
        await message.answer(str(button.get("action_value") or ""))
        return
    if action == "url" and button.get("action_value"):
        _user, configuration = await _user_and_configuration(telegram_id)
        language = normalize_language(configuration.get("language"))
        label = str(button.get(f"label_{language}") or button.get("label_fa") or "بازکردن لینک")
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


@router.callback_query(F.data == "support:open")
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
            await callback.answer("این دکمه دیگر فعال نیست.", show_alert=True)
            return
        await perform_custom_button(
            callback.message,
            state,
            telegram_id=callback.from_user.id,
            button=button,
        )
        await callback.answer()
    except (TypeError, ValueError):
        await callback.answer("دکمه معتبر نیست.", show_alert=True)


@router.callback_query(F.data.startswith("support:category:"))
async def choose_support_category(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    category = callback.data.rsplit(":", 1)[-1]
    if category not in SUPPORT_CATEGORY_LABELS["fa"]:
        await callback.answer("موضوع معتبر نیست.", show_alert=True)
        return
    _user, configuration = await _user_and_configuration(callback.from_user.id)
    await state.set_state(SupportStates.waiting_for_user_message)
    await state.update_data(support_category=category)
    await callback.message.edit_text(
        runtime_content(configuration, "support_prompt"),
    )
    await callback.message.answer(
        "پیام پشتیبانی را ارسال کنید:",
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer()


@router.callback_query(F.data == "support:cancel")
async def cancel_support(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("درخواست پشتیبانی لغو شد.")
    await callback.answer()


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
    category = SUPPORT_CATEGORY_LABELS["fa"].get(ticket.get("category"), "پشتیبانی")
    message = (ticket.get("messages") or [{}])[0]
    body = html.escape(str(message.get("body") or "[پیوست]"))
    return (
        f"🆕 <b>تیکت پشتیبانی #{ticket['id']}</b>\n\n"
        f"موضوع: <b>{html.escape(category)}</b>\n"
        f"نام: {html.escape(full_name)}\n"
        f"نام کاربری: {html.escape(username)}\n"
        f"Telegram ID: <code>{user.get('telegram_id')}</code>\n\n"
        f"{body}"
    )[:3900]


@router.message(SupportStates.waiting_for_user_message)
async def receive_support_message(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    category = str(data.get("support_category") or "")
    body = (message.text or message.caption or "").strip() or None
    file_id, file_type = _support_attachment(message)
    if body is None and file_id is None:
        await message.answer("لطفاً متن، تصویر، ویدئو، فایل یا پیام صوتی ارسال کنید.")
        return
    try:
        ticket = await create_support_ticket(
            telegram_id=message.from_user.id,
            category=category,
            body=body,
            telegram_file_id=file_id,
            file_type=file_type,
        )
        notification = _ticket_notification(ticket)
        delivered = 0
        for recipient in ticket.get("recipients", []):
            try:
                await message.bot.send_message(
                    chat_id=int(recipient),
                    text=notification,
                    parse_mode="HTML",
                    reply_markup=build_support_admin_keyboard(int(ticket["id"])),
                )
                if file_id is not None:
                    await message.bot.copy_message(
                        chat_id=int(recipient),
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                    )
                delivered += 1
            except Exception as exc:
                print(f"Support delivery failed for {recipient}: {type(exc).__name__}: {exc}")

        await state.clear()
        user, configuration = await _user_and_configuration(message.from_user.id)
        suffix = "" if delivered else "\n\n⚠️ مدیر فعالی برای دریافت فوری پیدا نشد؛ تیکت در پنل ذخیره شده است."
        await message.answer(
            f"{runtime_content(configuration, 'support_sent')}\nشناسه پیگیری: #{ticket['id']}{suffix}",
            reply_markup=build_home_reply_keyboard(
                normalize_language(configuration.get("language")),
                include_admin=bool(user.get("is_admin")),
                configuration=configuration,
            ),
        )
    except BackendAPIError:
        await message.answer("❌ ثبت درخواست پشتیبانی انجام نشد؛ کمی بعد دوباره تلاش کنید.")


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
        await callback.answer("عضویت در همه کانال‌ها هنوز تأیید نشده است.", show_alert=True)
        return
    await callback.message.edit_text(runtime_content(configuration, "membership_verified"))
    await callback.answer("عضویت تأیید شد.")


def _ticket_detail_text(ticket: dict) -> str:
    user = ticket.get("user") or {}
    category = SUPPORT_CATEGORY_LABELS["fa"].get(ticket.get("category"), "پشتیبانی")
    status = {"open": "باز", "answered": "پاسخ داده‌شده", "closed": "بسته"}.get(
        ticket.get("status"), "نامشخص"
    )
    lines = [
        f"🎫 <b>تیکت #{ticket['id']}</b>",
        "",
        f"موضوع: <b>{html.escape(category)}</b>",
        f"وضعیت: <b>{status}</b>",
        f"کاربر: <code>{user.get('telegram_id')}</code>",
        "",
    ]
    for item in (ticket.get("messages") or [])[-10:]:
        sender = "👤 کاربر" if item.get("sender_kind") == "user" else "👮 مدیر"
        body = html.escape(str(item.get("body") or f"[{item.get('file_type') or 'پیوست'}]"))
        lines.append(f"<b>{sender}:</b> {body}")
    return "\n".join(lines)[:3900]


@router.callback_query(F.data == "admin:support")
async def admin_support_list(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        context = await get_admin_context(callback.from_user.id)
        if not context.get("is_admin") or not _can(context, "tickets.view"):
            await callback.answer("دسترسی مشاهده تیکت‌ها ندارید.", show_alert=True)
            return
        await state.clear()
        tickets = await list_support_tickets(callback.from_user.id, status="open")
        await callback.message.edit_text(
            "🛟 <b>تیکت‌های باز پشتیبانی</b>\n\n"
            + (f"{len(tickets)} تیکت باز وجود دارد." if tickets else "تیکت بازی وجود ندارد."),
            parse_mode="HTML",
            reply_markup=build_support_ticket_list_keyboard(tickets),
        )
        await callback.answer()
    except BackendAPIError:
        await callback.answer("دریافت تیکت‌ها ممکن نشد.", show_alert=True)


@router.callback_query(F.data.startswith("admin:support:ticket:"))
async def admin_support_detail(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    try:
        ticket_id = int(callback.data.rsplit(":", 1)[-1])
        ticket = await get_support_ticket(
            actor_telegram_id=callback.from_user.id,
            ticket_id=ticket_id,
        )
        await callback.message.edit_text(
            _ticket_detail_text(ticket),
            parse_mode="HTML",
            reply_markup=build_support_ticket_detail_keyboard(
                ticket_id,
                is_closed=ticket.get("status") == "closed",
            ),
        )
        await callback.answer()
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer("دریافت تیکت ممکن نشد.", show_alert=True)


@router.callback_query(F.data.startswith("support_admin:reply:"))
async def begin_admin_support_reply(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    context = await get_admin_context(callback.from_user.id)
    if not context.get("is_admin") or not _can(context, "tickets.reply"):
        await callback.answer("دسترسی پاسخ‌گویی ندارید.", show_alert=True)
        return
    ticket_id = int(callback.data.rsplit(":", 1)[-1])
    await state.set_state(SupportStates.waiting_for_admin_reply)
    await state.update_data(support_ticket_id=ticket_id)
    await callback.message.answer(
        f"پاسخ تیکت #{ticket_id} را ارسال کنید:",
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
        user = ticket.get("user") or {}
        configuration = await runtime_configuration(
            normalize_language(user.get("effective_language"))
        )
        await message.bot.send_message(
            chat_id=int(user["telegram_id"]),
            text=(
                f"🛟 <b>پاسخ پشتیبانی — تیکت #{ticket_id}</b>\n\n"
                f"{html.escape(message.text)}"
            ),
            parse_mode="HTML",
            reply_markup=build_home_reply_keyboard(
                normalize_language(configuration.get("language")),
                include_admin=False,
                configuration=configuration,
            ),
        )
        await state.clear()
        await message.answer("✅ پاسخ ثبت و برای کاربر ارسال شد.")
    except (BackendAPIError, TypeError, ValueError, KeyError):
        await message.answer("❌ ثبت یا ارسال پاسخ انجام نشد.")


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
        await callback.answer("تیکت بسته شد.")
    except (BackendAPIError, TypeError, ValueError):
        await callback.answer("بستن تیکت انجام نشد.", show_alert=True)

