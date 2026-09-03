import html
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.keyboards.admin_finance import (
    PAYMENT_PAGE_SIZE,
    build_card_detail_keyboard,
    build_cards_keyboard,
    build_finance_cancel_keyboard,
    build_finance_confirmation_keyboard,
    build_finance_home_keyboard,
    build_payment_detail_keyboard,
    build_payment_list_keyboard,
    build_usdt_detail_keyboard,
    build_usdt_keyboard,
)
from app.keyboards.payment import format_toman
from app.services.backend import (
    BackendAPIError,
    create_payment_card,
    create_usdt_destination,
    delete_payment_card,
    delete_usdt_destination,
    get_admin_context,
    get_admin_payment,
    get_admin_payment_summary,
    get_payment_card,
    get_usdt_destination,
    list_admin_payments,
    list_payment_cards,
    list_usdt_destinations,
    update_payment_card,
    update_usdt_destination,
)
from app.state.admin_finance import AdminFinanceStates


router = Router(name="admin-finance")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
DISPLAY_TIMEZONE = ZoneInfo("Asia/Tehran")
_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


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


def _parse_integer(value: str) -> int | None:
    normalized = (
        str(value or "")
        .strip()
        .translate(_DIGIT_TRANSLATION)
        .replace(",", "")
        .replace("٬", "")
    )
    return int(normalized) if normalized.isdigit() else None


def _normalize_card_number(value: str) -> str | None:
    normalized = (
        str(value or "")
        .strip()
        .translate(_DIGIT_TRANSLATION)
        .replace(" ", "")
        .replace("-", "")
    )
    if len(normalized) != 16 or not normalized.isdigit():
        return None
    return normalized


def _format_card_number(value: object) -> str:
    number = str(value or "")
    return "-".join(number[index : index + 4] for index in range(0, 16, 4))


def _format_datetime(value: object) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)[:30]


def _finance_error_text(exc: BackendAPIError) -> str:
    if exc.status_code == 403:
        return "دسترسی لازم برای این عملیات را ندارید."
    if exc.status_code == 404:
        return "رکورد موردنظر پیدا نشد؛ فهرست را دوباره باز کنید."
    if exc.status_code == 409:
        return "این اطلاعات تکراری است یا با وضعیت فعلی تداخل دارد."
    if exc.status_code == 422:
        return "اطلاعات واردشده معتبر نیست."
    return "ارتباط با بخش مالی انجام نشد؛ کمی بعد دوباره تلاش کنید."


async def _show_finance_home(message: Message, actor_telegram_id: int) -> None:
    context = await _context(actor_telegram_id, "payments.view")
    if context is None:
        return
    summary = await get_admin_payment_summary(actor_telegram_id)
    card_note = ""
    if int(summary.get("active_cards", 0)) == 0:
        if int(summary.get("cards", 0)) > 0:
            card_note = (
                "\n\n⚠️ همه کارت‌های دیتابیسی غیرفعال‌اند؛ خرید جدید تا فعال‌کردن "
                "یک کارت انجام نمی‌شود."
            )
        elif summary.get("legacy_card_configured"):
            card_note = (
                "\n\nℹ️ تا زمان افزودن کارت دیتابیسی، کارت قدیمی سرور "
                "برای خریدها استفاده می‌شود."
            )
        else:
            card_note = "\n\n⚠️ هیچ کارت فعالی برای دریافت وجه وجود ندارد."
    await message.edit_text(
        (
            "💳 <b>مدیریت پرداخت‌ها</b>\n\n"
            f"⏳ در انتظار بررسی: <code>{int(summary['pending'])}</code>\n"
            f"✅ تأییدشده: <code>{int(summary['approved'])}</code>\n"
            f"❌ ردشده: <code>{int(summary['rejected'])}</code>\n\n"
            f"💳 کارت‌های فعال: <code>{int(summary['active_cards'])}"
            f" از {int(summary['cards'])}</code>\n"
            "💵 مقصدهای فعال USDT: "
            f"<code>{int(summary['active_usdt_destinations'])}"
            f" از {int(summary['usdt_destinations'])}</code>"
            f"{card_note}"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_home_keyboard(
            can_manage_destinations=_can(
                context,
                "payment_destinations.manage",
            )
        ),
    )


async def _show_cards(message: Message, actor_telegram_id: int) -> None:
    cards = await list_payment_cards(actor_telegram_id)
    active_count = sum(bool(card.get("is_active")) for card in cards)
    await message.edit_text(
        (
            "💳 <b>شماره کارت‌ها</b>\n\n"
            "کارت‌های فعال به‌صورت نوبتی و اتمیک بین خریدهای جدید "
            "انتخاب می‌شوند.\n\n"
            f"تعداد کل: <code>{len(cards)}</code>\n"
            f"فعال: <code>{active_count}</code>"
        ),
        parse_mode="HTML",
        reply_markup=build_cards_keyboard(cards),
    )


async def _show_usdt(message: Message, actor_telegram_id: int) -> None:
    destinations = await list_usdt_destinations(actor_telegram_id)
    active_count = sum(
        bool(destination.get("is_active")) for destination in destinations
    )
    await message.edit_text(
        (
            "💵 <b>کیف‌پول‌های USDT</b>\n\n"
            "در این بخش فقط آدرس عمومی دریافت ثبت می‌شود؛ هرگز کلید خصوصی "
            "یا عبارت بازیابی وارد نکنید.\n\n"
            f"تعداد کل: <code>{len(destinations)}</code>\n"
            f"فعال: <code>{active_count}</code>"
        ),
        parse_mode="HTML",
        reply_markup=build_usdt_keyboard(destinations),
    )


def _card_text(card: dict) -> str:
    status = "فعال ✅" if card.get("is_active") else "غیرفعال ⛔️"
    return (
        "💳 <b>مشخصات کارت</b>\n\n"
        f"عنوان: <b>{html.escape(str(card.get('label') or '—'))}</b>\n"
        f"شماره: <code>{_format_card_number(card.get('card_number'))}</code>\n"
        f"صاحب کارت: <b>{html.escape(str(card.get('card_holder') or '—'))}</b>\n"
        f"بانک: <b>{html.escape(str(card.get('bank_name') or '—'))}</b>\n"
        f"وضعیت: <b>{status}</b>\n"
        f"ترتیب: <code>{int(card.get('sort_order', 0))}</code>\n"
        f"دفعات انتخاب: <code>{int(card.get('selection_count', 0))}</code>\n"
        f"آخرین انتخاب: <code>{_format_datetime(card.get('last_selected_at'))}</code>"
    )


def _usdt_text(destination: dict) -> str:
    status = "فعال ✅" if destination.get("is_active") else "غیرفعال ⛔️"
    return (
        "💵 <b>مشخصات مقصد USDT</b>\n\n"
        f"عنوان: <b>{html.escape(str(destination.get('label') or '—'))}</b>\n"
        "شبکه: "
        f"<b>{html.escape(str(destination.get('network_name') or '—'))}</b> "
        f"(<code>{html.escape(str(destination.get('network_code') or '—'))}</code>)\n"
        f"دارایی: <code>{html.escape(str(destination.get('asset_symbol') or 'USDT'))}</code>\n"
        f"آدرس: <code>{html.escape(str(destination.get('address') or '—'))}</code>\n"
        "تأیید لازم: "
        f"<code>{int(destination.get('confirmations_required', 20))}</code>\n"
        f"قرارداد: <code>{html.escape(str(destination.get('contract_address') or '—'))}</code>\n"
        f"مرورگر: <code>{html.escape(str(destination.get('explorer_url') or '—'))}</code>\n"
        f"وضعیت: <b>{status}</b>\n"
        f"ترتیب: <code>{int(destination.get('sort_order', 0))}</code>"
    )


def _payment_caption(payment: dict) -> str:
    statuses = {
        "pending": "در انتظار بررسی ⏳",
        "approved": "تأییدشده ✅",
        "rejected": "ردشده ❌",
    }
    full_name = " ".join(
        str(value)
        for value in [payment.get("first_name"), payment.get("last_name")]
        if value
    ).strip() or "—"
    username = payment.get("username")
    identity = f"@{username}" if username else "—"
    destination = payment.get("payment_destination_snapshot") or {}
    card_number = str(destination.get("card_number") or "")
    card_line = (
        f"\n💳 کارت مقصد: <code>**** {card_number[-4:]}</code>"
        if card_number
        else ""
    )
    rejection = ""
    if payment.get("rejection_reason"):
        rejection = (
            "\n📝 دلیل رد: "
            f"{html.escape(str(payment['rejection_reason'])[:300])}"
        )
    return (
        "🧾 <b>جزئیات پرداخت</b>\n\n"
        f"شناسه: <code>{int(payment['id'])}</code>\n"
        f"وضعیت: <b>{statuses.get(str(payment.get('status')), 'نامشخص')}</b>\n"
        f"کاربر: {html.escape(full_name)} — {html.escape(identity)}\n"
        f"Telegram ID: <code>{int(payment['user_telegram_id'])}</code>\n\n"
        f"پلن: <b>{html.escape(str(payment.get('plan_name_snapshot') or '—'))}</b>\n"
        f"مدت: <code>{int(payment.get('duration_days', 0))} روز</code>\n"
        f"مبلغ: <b>{format_toman(payment.get('amount'))}</b>"
        f"{card_line}\n"
        f"ثبت: <code>{_format_datetime(payment.get('created_at'))}</code>"
        f"{rejection}"
    )[:1024]


@router.callback_query(F.data == "admin:payments")
async def show_finance_home(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    context = await _context(callback.from_user.id, "payments.view")
    if context is None:
        await callback.answer("دسترسی مشاهده پرداخت‌ها ندارید.", show_alert=True)
        return
    try:
        await state.clear()
        await _show_finance_home(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)


@router.callback_query(F.data.startswith("admin:pay:list:"))
async def show_payment_list(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(callback.from_user.id, "payments.view") is None:
        await callback.answer("دسترسی مشاهده پرداخت‌ها ندارید.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        return
    status_filter = parts[3]
    page = _parse_integer(parts[4]) or 1
    status = None if status_filter == "all" else status_filter
    try:
        await state.clear()
        result = await list_admin_payments(
            callback.from_user.id,
            status=status,
            page=page,
            page_size=PAYMENT_PAGE_SIZE,
        )
        total = int(result.get("total", 0))
        label = "همه پرداخت‌ها" if status is None else "رسیدهای در انتظار"
        await callback.message.edit_text(
            (
                f"🧾 <b>{label}</b>\n\n"
                f"تعداد: <code>{total}</code> — صفحه <code>{page}</code>"
            ),
            parse_mode="HTML",
            reply_markup=build_payment_list_keyboard(
                list(result.get("items", [])),
                status_filter=status_filter,
                page=page,
                total=total,
            ),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)


@router.callback_query(F.data.startswith("admin:pay:view:"))
async def show_payment_receipt(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    context = await _context(callback.from_user.id, "payments.view")
    if context is None:
        await callback.answer("دسترسی مشاهده پرداخت‌ها ندارید.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 6:
        return
    payment_id = _parse_integer(parts[3])
    status_filter = parts[4]
    page = _parse_integer(parts[5]) or 1
    if payment_id is None:
        return
    try:
        payment = await get_admin_payment(
            actor_telegram_id=callback.from_user.id,
            payment_id=payment_id,
        )
        kwargs = {
            "chat_id": callback.message.chat.id,
            "caption": _payment_caption(payment),
            "parse_mode": "HTML",
            "reply_markup": build_payment_detail_keyboard(
                payment,
                can_review=_can(context, "payments.review"),
                status_filter=status_filter,
                page=page,
            ),
        }
        if payment.get("receipt_file_type") == "photo":
            await callback.message.bot.send_photo(
                photo=payment["receipt_file_id"],
                **kwargs,
            )
        else:
            await callback.message.bot.send_document(
                document=payment["receipt_file_id"],
                **kwargs,
            )
        await callback.answer("رسید نمایش داده شد.")
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
    except Exception:
        await callback.answer(
            "تلگرام نتوانست فایل رسید را نمایش دهد.",
            show_alert=True,
        )


@router.callback_query(F.data == "admin:cards")
async def show_cards(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کارت‌ها ندارید.", show_alert=True)
        return
    try:
        await state.clear()
        await _show_cards(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:card:\d+$"))
async def show_card_detail(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کارت‌ها ندارید.", show_alert=True)
        return
    try:
        card = await get_payment_card(
            actor_telegram_id=callback.from_user.id,
            card_id=int(callback.data.rsplit(":", 1)[-1]),
        )
        await callback.message.edit_text(
            _card_text(card),
            parse_mode="HTML",
            reply_markup=build_card_detail_keyboard(card),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)


async def _ask_reply(message: Message, text: str) -> None:
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )


@router.callback_query(F.data == "admin:card:add")
async def start_card_create(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کارت‌ها ندارید.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        workflow="card_create",
        card_data={},
        return_to="cards",
    )
    await state.set_state(AdminFinanceStates.waiting_for_card_label)
    await callback.message.edit_text(
        (
            "➕ <b>افزودن کارت</b>\n\n"
            "یک عنوان قابل تشخیص وارد کنید؛ مثلاً «کارت اصلی»."
        ),
        parse_mode="HTML",
        reply_markup=build_finance_cancel_keyboard(),
    )
    await _ask_reply(callback.message, "عنوان کارت را بفرستید:")
    await callback.answer()


@router.message(AdminFinanceStates.waiting_for_card_label)
async def receive_card_label(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 2 <= len(value) <= 100:
        await message.answer("عنوان باید بین ۲ تا ۱۰۰ نویسه باشد.")
        return
    data = await state.get_data()
    card_data = dict(data.get("card_data") or {})
    card_data["label"] = value
    await state.update_data(card_data=card_data)
    await state.set_state(AdminFinanceStates.waiting_for_card_number)
    await _ask_reply(message, "شماره کارت ۱۶ رقمی را بفرستید:")


@router.message(AdminFinanceStates.waiting_for_card_number)
async def receive_card_number(message: Message, state: FSMContext) -> None:
    value = _normalize_card_number(str(message.text or ""))
    if value is None:
        await message.answer("شماره کارت باید دقیقاً ۱۶ رقم باشد.")
        return
    data = await state.get_data()
    card_data = dict(data.get("card_data") or {})
    card_data["card_number"] = value
    await state.update_data(card_data=card_data)
    await state.set_state(AdminFinanceStates.waiting_for_card_holder)
    await _ask_reply(message, "نام صاحب کارت را بفرستید:")


@router.message(AdminFinanceStates.waiting_for_card_holder)
async def receive_card_holder(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 2 <= len(value) <= 120:
        await message.answer("نام صاحب کارت باید بین ۲ تا ۱۲۰ نویسه باشد.")
        return
    data = await state.get_data()
    card_data = dict(data.get("card_data") or {})
    card_data["card_holder"] = value
    await state.update_data(card_data=card_data)
    await state.set_state(AdminFinanceStates.waiting_for_card_bank)
    await _ask_reply(
        message,
        "نام بانک را بفرستید؛ اگر نمی‌خواهید نمایش داده شود، <code>-</code> بفرستید:",
    )


@router.message(AdminFinanceStates.waiting_for_card_bank)
async def receive_card_bank(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if len(value) > 100:
        await message.answer("نام بانک حداکثر ۱۰۰ نویسه است.")
        return
    data = await state.get_data()
    card_data = dict(data.get("card_data") or {})
    card_data["bank_name"] = None if value == "-" else value
    await state.update_data(card_data=card_data)
    await state.set_state(AdminFinanceStates.waiting_for_card_order)
    await _ask_reply(
        message,
        "ترتیب نمایش را به‌صورت عدد صفر یا بزرگ‌تر بفرستید:",
    )


def _card_review_text(card_data: dict, *, action: str) -> str:
    return (
        f"💳 <b>{html.escape(action)}</b>\n\n"
        f"عنوان: <b>{html.escape(str(card_data.get('label') or '—'))}</b>\n"
        f"شماره: <code>{_format_card_number(card_data.get('card_number'))}</code>\n"
        f"صاحب کارت: <b>{html.escape(str(card_data.get('card_holder') or '—'))}</b>\n"
        f"بانک: <b>{html.escape(str(card_data.get('bank_name') or '—'))}</b>\n"
        f"ترتیب: <code>{int(card_data.get('sort_order', 0))}</code>\n\n"
        "اطلاعات را بررسی و ثبت را تأیید کنید."
    )


@router.message(AdminFinanceStates.waiting_for_card_order)
async def receive_card_order(message: Message, state: FSMContext) -> None:
    value = _parse_integer(str(message.text or ""))
    if value is None or value > 100000:
        await message.answer("ترتیب باید عددی بین صفر تا ۱۰۰۰۰۰ باشد.")
        return
    data = await state.get_data()
    card_data = dict(data.get("card_data") or {})
    card_data.update(sort_order=value, is_active=True)
    await state.update_data(card_data=card_data)
    await state.set_state(AdminFinanceStates.confirming_action)
    await message.answer(
        _card_review_text(card_data, action="مرور کارت جدید"),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )


_CARD_EDIT_FIELDS = {
    "label": ("label", "عنوان جدید کارت را بفرستید:"),
    "number": ("card_number", "شماره کارت ۱۶ رقمی جدید را بفرستید:"),
    "holder": ("card_holder", "نام جدید صاحب کارت را بفرستید:"),
    "bank": ("bank_name", "نام بانک یا - برای حذف آن را بفرستید:"),
    "order": ("sort_order", "ترتیب جدید، از صفر تا ۱۰۰۰۰۰، را بفرستید:"),
}


@router.callback_query(F.data.startswith("admin:card:edit:"))
async def start_card_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کارت‌ها ندارید.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5 or parts[3] not in _CARD_EDIT_FIELDS:
        return
    field_alias = parts[3]
    card_id = int(parts[4])
    try:
        card = await get_payment_card(
            actor_telegram_id=callback.from_user.id,
            card_id=card_id,
        )
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
        return
    api_field, prompt = _CARD_EDIT_FIELDS[field_alias]
    await state.clear()
    await state.update_data(
        workflow="card_update",
        return_to="cards",
        card_id=card_id,
        card=card,
        edit_field=api_field,
    )
    await state.set_state(AdminFinanceStates.waiting_for_card_edit_value)
    await callback.message.edit_text(
        (
            "✏️ <b>ویرایش کارت</b>\n\n"
            f"کارت: <b>{html.escape(str(card.get('label') or '—'))}</b>"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_cancel_keyboard(),
    )
    await _ask_reply(callback.message, prompt)
    await callback.answer()


@router.message(AdminFinanceStates.waiting_for_card_edit_value)
async def receive_card_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = str(data.get("edit_field") or "")
    raw = str(message.text or "").strip()
    value: object
    if field == "card_number":
        normalized = _normalize_card_number(raw)
        if normalized is None:
            await message.answer("شماره کارت باید دقیقاً ۱۶ رقم باشد.")
            return
        value = normalized
    elif field == "sort_order":
        parsed = _parse_integer(raw)
        if parsed is None or parsed > 100000:
            await message.answer("ترتیب باید بین صفر تا ۱۰۰۰۰۰ باشد.")
            return
        value = parsed
    elif field == "bank_name":
        if len(raw) > 100:
            await message.answer("نام بانک حداکثر ۱۰۰ نویسه است.")
            return
        value = None if raw == "-" else raw
    else:
        maximum = 100 if field == "label" else 120
        if not 2 <= len(raw) <= maximum:
            await message.answer(f"مقدار باید بین ۲ تا {maximum} نویسه باشد.")
            return
        value = raw

    await state.update_data(pending_changes={field: value})
    await state.set_state(AdminFinanceStates.confirming_action)
    await message.answer(
        (
            "✏️ <b>تأیید ویرایش کارت</b>\n\n"
            f"فیلد: <code>{html.escape(field)}</code>\n"
            f"مقدار جدید: <code>{html.escape(str(value or 'خالی'))}</code>"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:card:toggle:"))
async def prepare_card_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کارت‌ها ندارید.", show_alert=True)
        return
    try:
        card_id = int(callback.data.rsplit(":", 1)[-1])
        card = await get_payment_card(
            actor_telegram_id=callback.from_user.id,
            card_id=card_id,
        )
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
        return
    new_status = not bool(card.get("is_active"))
    await state.clear()
    await state.update_data(
        workflow="card_update",
        return_to="cards",
        card_id=card_id,
        pending_changes={"is_active": new_status},
    )
    await state.set_state(AdminFinanceStates.confirming_action)
    await callback.message.edit_text(
        (
            "⚠️ <b>تأیید تغییر وضعیت کارت</b>\n\n"
            f"کارت: <b>{html.escape(str(card.get('label') or '—'))}</b>\n"
            f"وضعیت جدید: <b>{'فعال' if new_status else 'غیرفعال'}</b>"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:card:delete:"))
async def prepare_card_delete(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کارت‌ها ندارید.", show_alert=True)
        return
    try:
        card_id = int(callback.data.rsplit(":", 1)[-1])
        card = await get_payment_card(
            actor_telegram_id=callback.from_user.id,
            card_id=card_id,
        )
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
        return
    await state.clear()
    await state.update_data(
        workflow="card_delete",
        return_to="cards",
        card_id=card_id,
    )
    await state.set_state(AdminFinanceStates.confirming_action)
    await callback.message.edit_text(
        (
            "🚨 <b>حذف کارت</b>\n\n"
            f"کارت «{html.escape(str(card.get('label') or '—'))}» حذف شود؟\n"
            "سوابق پرداخت، Snapshot کارت خود را حفظ می‌کنند."
        ),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:usdt")
async def show_usdt(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کیف‌پول‌ها ندارید.", show_alert=True)
        return
    try:
        await state.clear()
        await _show_usdt(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:usdt:item:\d+$"))
async def show_usdt_detail(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کیف‌پول‌ها ندارید.", show_alert=True)
        return
    try:
        destination = await get_usdt_destination(
            actor_telegram_id=callback.from_user.id,
            destination_id=int(callback.data.rsplit(":", 1)[-1]),
        )
        await callback.message.edit_text(
            _usdt_text(destination),
            parse_mode="HTML",
            reply_markup=build_usdt_detail_keyboard(destination),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:usdt:add")
async def start_usdt_create(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کیف‌پول‌ها ندارید.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        workflow="usdt_create",
        usdt_data={"asset_symbol": "USDT", "is_active": True},
        return_to="usdt",
    )
    await state.set_state(AdminFinanceStates.waiting_for_usdt_label)
    await callback.message.edit_text(
        (
            "➕ <b>افزودن کیف‌پول USDT</b>\n\n"
            "فقط آدرس عمومی دریافت را وارد کنید؛ کلید خصوصی یا عبارت بازیابی "
            "نباید در ربات ثبت شود."
        ),
        parse_mode="HTML",
        reply_markup=build_finance_cancel_keyboard(),
    )
    await _ask_reply(callback.message, "عنوان مقصد را بفرستید؛ مثلاً «TRC20 اصلی»:")
    await callback.answer()


async def _update_usdt_data(
    state: FSMContext,
    field: str,
    value: object,
) -> None:
    data = await state.get_data()
    usdt_data = dict(data.get("usdt_data") or {})
    usdt_data[field] = value
    await state.update_data(usdt_data=usdt_data)


@router.message(AdminFinanceStates.waiting_for_usdt_label)
async def receive_usdt_label(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 2 <= len(value) <= 100:
        await message.answer("عنوان باید بین ۲ تا ۱۰۰ نویسه باشد.")
        return
    await _update_usdt_data(state, "label", value)
    await state.set_state(AdminFinanceStates.waiting_for_usdt_network_name)
    await _ask_reply(message, "نام کامل شبکه را بفرستید؛ مثلاً TRON:")


@router.message(AdminFinanceStates.waiting_for_usdt_network_name)
async def receive_usdt_network_name(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 2 <= len(value) <= 100:
        await message.answer("نام شبکه باید بین ۲ تا ۱۰۰ نویسه باشد.")
        return
    await _update_usdt_data(state, "network_name", value)
    await state.set_state(AdminFinanceStates.waiting_for_usdt_network_code)
    await _ask_reply(message, "کد شبکه را بفرستید؛ مثلاً TRC20:")


@router.message(AdminFinanceStates.waiting_for_usdt_network_code)
async def receive_usdt_network_code(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip().upper()
    if not 2 <= len(value) <= 32 or any(character.isspace() for character in value):
        await message.answer("کد شبکه باید ۲ تا ۳۲ نویسه و بدون فاصله باشد.")
        return
    await _update_usdt_data(state, "network_code", value)
    await state.set_state(AdminFinanceStates.waiting_for_usdt_address)
    await _ask_reply(message, "آدرس عمومی دریافت USDT را بفرستید:")


@router.message(AdminFinanceStates.waiting_for_usdt_address)
async def receive_usdt_address(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if not 10 <= len(value) <= 255 or any(
        character.isspace() for character in value
    ):
        await message.answer("آدرس باید ۱۰ تا ۲۵۵ نویسه و بدون فاصله باشد.")
        return
    await _update_usdt_data(state, "address", value)
    await state.set_state(AdminFinanceStates.waiting_for_usdt_confirmations)
    await _ask_reply(message, "تعداد تأیید شبکه لازم را بفرستید؛ مثلاً ۲۰:")


@router.message(AdminFinanceStates.waiting_for_usdt_confirmations)
async def receive_usdt_confirmations(
    message: Message,
    state: FSMContext,
) -> None:
    value = _parse_integer(str(message.text or ""))
    if value is None or not 1 <= value <= 1000:
        await message.answer("تعداد تأیید باید بین ۱ تا ۱۰۰۰ باشد.")
        return
    await _update_usdt_data(state, "confirmations_required", value)
    await state.set_state(AdminFinanceStates.waiting_for_usdt_contract)
    await _ask_reply(
        message,
        "آدرس قرارداد توکن را بفرستید؛ اگر لازم نیست، <code>-</code> بفرستید:",
    )


@router.message(AdminFinanceStates.waiting_for_usdt_contract)
async def receive_usdt_contract(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if len(value) > 255:
        await message.answer("آدرس قرارداد حداکثر ۲۵۵ نویسه است.")
        return
    await _update_usdt_data(
        state,
        "contract_address",
        None if value == "-" else value,
    )
    await state.set_state(AdminFinanceStates.waiting_for_usdt_explorer)
    await _ask_reply(
        message,
        "نشانی مرورگر شبکه را بفرستید؛ اگر لازم نیست، <code>-</code> بفرستید:",
    )


@router.message(AdminFinanceStates.waiting_for_usdt_explorer)
async def receive_usdt_explorer(message: Message, state: FSMContext) -> None:
    value = str(message.text or "").strip()
    if value != "-" and (
        len(value) > 500 or not value.startswith(("https://", "http://"))
    ):
        await message.answer("لینک باید با https:// یا http:// شروع شود.")
        return
    await _update_usdt_data(
        state,
        "explorer_url",
        None if value == "-" else value,
    )
    await state.set_state(AdminFinanceStates.waiting_for_usdt_order)
    await _ask_reply(message, "ترتیب نمایش را به‌صورت عدد صفر یا بزرگ‌تر بفرستید:")


def _usdt_review_text(data: dict, *, action: str) -> str:
    return (
        f"💵 <b>{html.escape(action)}</b>\n\n"
        f"عنوان: <b>{html.escape(str(data.get('label') or '—'))}</b>\n"
        f"شبکه: <b>{html.escape(str(data.get('network_name') or '—'))}</b> "
        f"(<code>{html.escape(str(data.get('network_code') or '—'))}</code>)\n"
        f"آدرس: <code>{html.escape(str(data.get('address') or '—'))}</code>\n"
        f"تأیید لازم: <code>{int(data.get('confirmations_required', 20))}</code>\n"
        f"ترتیب: <code>{int(data.get('sort_order', 0))}</code>\n\n"
        "اطلاعات شبکه و آدرس را با دقت بررسی کنید."
    )


@router.message(AdminFinanceStates.waiting_for_usdt_order)
async def receive_usdt_order(message: Message, state: FSMContext) -> None:
    value = _parse_integer(str(message.text or ""))
    if value is None or value > 100000:
        await message.answer("ترتیب باید عددی بین صفر تا ۱۰۰۰۰۰ باشد.")
        return
    await _update_usdt_data(state, "sort_order", value)
    data = await state.get_data()
    await state.set_state(AdminFinanceStates.confirming_action)
    await message.answer(
        _usdt_review_text(
            dict(data.get("usdt_data") or {}) | {"sort_order": value},
            action="مرور کیف‌پول جدید",
        ),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )


_USDT_EDIT_FIELDS = {
    "label": "عنوان جدید را بفرستید:",
    "network_name": "نام کامل جدید شبکه را بفرستید:",
    "network_code": "کد جدید شبکه را بفرستید:",
    "address": "آدرس عمومی جدید را بفرستید:",
    "confirmations_required": "تعداد تأیید جدید را بفرستید:",
    "sort_order": "ترتیب جدید را بفرستید:",
    "contract_address": "آدرس قرارداد یا - برای حذف را بفرستید:",
    "explorer_url": "لینک مرورگر یا - برای حذف را بفرستید:",
}


@router.callback_query(F.data.startswith("admin:usdt:edit:"))
async def start_usdt_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کیف‌پول‌ها ندارید.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5 or parts[3] not in _USDT_EDIT_FIELDS:
        return
    field = parts[3]
    destination_id = int(parts[4])
    try:
        destination = await get_usdt_destination(
            actor_telegram_id=callback.from_user.id,
            destination_id=destination_id,
        )
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
        return
    await state.clear()
    await state.update_data(
        workflow="usdt_update",
        return_to="usdt",
        destination_id=destination_id,
        destination=destination,
        edit_field=field,
    )
    await state.set_state(AdminFinanceStates.waiting_for_usdt_edit_value)
    await callback.message.edit_text(
        (
            "✏️ <b>ویرایش مقصد USDT</b>\n\n"
            f"مقصد: <b>{html.escape(str(destination.get('label') or '—'))}</b>"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_cancel_keyboard(),
    )
    await _ask_reply(callback.message, _USDT_EDIT_FIELDS[field])
    await callback.answer()


@router.message(AdminFinanceStates.waiting_for_usdt_edit_value)
async def receive_usdt_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = str(data.get("edit_field") or "")
    raw = str(message.text or "").strip()
    value: object
    if field in {"confirmations_required", "sort_order"}:
        parsed = _parse_integer(raw)
        maximum = 1000 if field == "confirmations_required" else 100000
        minimum = 1 if field == "confirmations_required" else 0
        if parsed is None or not minimum <= parsed <= maximum:
            await message.answer(f"عدد باید بین {minimum} تا {maximum} باشد.")
            return
        value = parsed
    elif field == "address":
        if not 10 <= len(raw) <= 255 or any(
            character.isspace() for character in raw
        ):
            await message.answer("آدرس باید ۱۰ تا ۲۵۵ نویسه و بدون فاصله باشد.")
            return
        value = raw
    elif field == "network_code":
        value = raw.upper()
        if not 2 <= len(str(value)) <= 32 or any(
            character.isspace() for character in str(value)
        ):
            await message.answer("کد شبکه باید ۲ تا ۳۲ نویسه و بدون فاصله باشد.")
            return
    elif field in {"contract_address", "explorer_url"}:
        value = None if raw == "-" else raw
        maximum = 255 if field == "contract_address" else 500
        if value is not None and len(str(value)) > maximum:
            await message.answer(f"مقدار حداکثر {maximum} نویسه است.")
            return
        if field == "explorer_url" and value is not None and not str(value).startswith(
            ("https://", "http://")
        ):
            await message.answer("لینک باید با https:// یا http:// شروع شود.")
            return
    else:
        if not 2 <= len(raw) <= 100:
            await message.answer("مقدار باید بین ۲ تا ۱۰۰ نویسه باشد.")
            return
        value = raw

    await state.update_data(pending_changes={field: value})
    await state.set_state(AdminFinanceStates.confirming_action)
    await message.answer(
        (
            "✏️ <b>تأیید ویرایش مقصد USDT</b>\n\n"
            f"فیلد: <code>{html.escape(field)}</code>\n"
            f"مقدار جدید: <code>{html.escape(str(value or 'خالی'))}</code>"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:usdt:toggle:"))
async def prepare_usdt_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کیف‌پول‌ها ندارید.", show_alert=True)
        return
    try:
        destination_id = int(callback.data.rsplit(":", 1)[-1])
        destination = await get_usdt_destination(
            actor_telegram_id=callback.from_user.id,
            destination_id=destination_id,
        )
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
        return
    new_status = not bool(destination.get("is_active"))
    await state.clear()
    await state.update_data(
        workflow="usdt_update",
        return_to="usdt",
        destination_id=destination_id,
        pending_changes={"is_active": new_status},
    )
    await state.set_state(AdminFinanceStates.confirming_action)
    await callback.message.edit_text(
        (
            "⚠️ <b>تأیید تغییر وضعیت کیف‌پول</b>\n\n"
            f"مقصد: <b>{html.escape(str(destination.get('label') or '—'))}</b>\n"
            f"وضعیت جدید: <b>{'فعال' if new_status else 'غیرفعال'}</b>"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:usdt:delete:"))
async def prepare_usdt_delete(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی مدیریت کیف‌پول‌ها ندارید.", show_alert=True)
        return
    try:
        destination_id = int(callback.data.rsplit(":", 1)[-1])
        destination = await get_usdt_destination(
            actor_telegram_id=callback.from_user.id,
            destination_id=destination_id,
        )
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
        return
    await state.clear()
    await state.update_data(
        workflow="usdt_delete",
        return_to="usdt",
        destination_id=destination_id,
    )
    await state.set_state(AdminFinanceStates.confirming_action)
    await callback.message.edit_text(
        (
            "🚨 <b>حذف کیف‌پول USDT</b>\n\n"
            f"مقصد «{html.escape(str(destination.get('label') or '—'))}» "
            "به‌طور کامل حذف شود؟"
        ),
        parse_mode="HTML",
        reply_markup=build_finance_confirmation_keyboard(),
    )
    await callback.answer()


@router.callback_query(
    AdminFinanceStates.confirming_action,
    F.data == "admin:finance:confirm",
)
async def confirm_finance_action(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    if await _context(
        callback.from_user.id,
        "payment_destinations.manage",
    ) is None:
        await callback.answer("دسترسی لازم را ندارید.", show_alert=True)
        return
    data = await state.get_data()
    workflow = str(data.get("workflow") or "")
    try:
        if workflow == "card_create":
            await create_payment_card(
                actor_telegram_id=callback.from_user.id,
                data=dict(data.get("card_data") or {}),
            )
        elif workflow == "card_update":
            await update_payment_card(
                actor_telegram_id=callback.from_user.id,
                card_id=int(data["card_id"]),
                changes=dict(data.get("pending_changes") or {}),
            )
        elif workflow == "card_delete":
            await delete_payment_card(
                actor_telegram_id=callback.from_user.id,
                card_id=int(data["card_id"]),
            )
        elif workflow == "usdt_create":
            await create_usdt_destination(
                actor_telegram_id=callback.from_user.id,
                data=dict(data.get("usdt_data") or {}),
            )
        elif workflow == "usdt_update":
            await update_usdt_destination(
                actor_telegram_id=callback.from_user.id,
                destination_id=int(data["destination_id"]),
                changes=dict(data.get("pending_changes") or {}),
            )
        elif workflow == "usdt_delete":
            await delete_usdt_destination(
                actor_telegram_id=callback.from_user.id,
                destination_id=int(data["destination_id"]),
            )
        else:
            await callback.answer("درخواست منقضی شده است.", show_alert=True)
            await state.clear()
            return

        return_to = str(data.get("return_to") or "payments")
        await state.clear()
        if return_to == "cards":
            await _show_cards(callback.message, callback.from_user.id)
        elif return_to == "usdt":
            await _show_usdt(callback.message, callback.from_user.id)
        else:
            await _show_finance_home(callback.message, callback.from_user.id)
        await callback.answer("تغییر با موفقیت ثبت شد.")
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)


@router.callback_query(F.data == "admin:finance:cancel")
async def cancel_finance_action(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    return_to = str(data.get("return_to") or "payments")
    await state.clear()
    try:
        if return_to == "cards":
            await _show_cards(callback.message, callback.from_user.id)
        elif return_to == "usdt":
            await _show_usdt(callback.message, callback.from_user.id)
        else:
            await _show_finance_home(callback.message, callback.from_user.id)
        await callback.answer("لغو شد.")
    except BackendAPIError as exc:
        await callback.answer(_finance_error_text(exc), show_alert=True)
