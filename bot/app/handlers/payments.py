import html
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, ForceReply, Message

from app.keyboards.payment import (
    build_admin_payment_keyboard,
    build_home_keyboard,
    build_home_reply_keyboard,
    build_payment_offer_detail_keyboard,
    build_payment_offers_keyboard,
    build_receipt_cancel_keyboard,
    build_usdt_destination_keyboard,
    format_toman,
    format_usdt,
    format_usdt_network,
)
from app.i18n import normalize_language
from app.runtime_config import runtime_configuration
from app.services.backend import (
    BackendAPIError,
    approve_manual_payment,
    create_manual_payment,
    get_admin_context,
    get_current_subscription,
    get_telegram_user,
    get_payment_configuration,
    mark_payment_delivery_failed,
    register_telegram_user,
    reject_manual_payment,
    set_payment_admin_message,
)
from app.state.payment import AdminPaymentStates, PaymentStates
from app.utils.formatting import format_date_for_language, format_quality_limit
from app.utils.payment_qr import build_usdt_address_qr


router = Router(name="payments")
logger = logging.getLogger(__name__)


def _parse_int_env(name: str) -> int | None:
    raw_value = os.getenv(name, "").strip()

    if not raw_value:
        return None

    try:
        return int(raw_value)
    except ValueError:
        return None


ADMIN_PAYMENT_CHAT_ID = _parse_int_env("ADMIN_NOTIFICATIONS_CHAT_ID")
ADMIN_PAYMENT_TOPIC_ID = _parse_int_env(
    "ADMIN_NOTIFICATIONS_PAYMENTS_TOPIC_ID"
)
DISPLAY_TIMEZONE = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Tehran"))


async def _user_home_reply_keyboard(user: dict):
    language = normalize_language(
        user.get("effective_language") or user.get("language_code")
    )
    configuration = await runtime_configuration(language)
    return build_home_reply_keyboard(
        language=language,
        include_admin=bool(user.get("is_admin")),
        configuration=configuration,
    )


async def _user_home_inline_keyboard(telegram_id: int):
    try:
        user = await get_telegram_user(telegram_id)
    except BackendAPIError:
        user = {"effective_language": "fa", "is_admin": False}
    language = normalize_language(user.get("effective_language"))
    configuration = await runtime_configuration(language)
    return build_home_keyboard(
        language,
        include_admin=bool(user.get("is_admin")),
        configuration=configuration,
    )


def _find_offer(configuration: dict, code: str) -> dict | None:
    return next(
        (
            offer
            for offer in configuration.get("offers", [])
            if offer.get("code") == code
        ),
        None,
    )


def _format_datetime(value: str | None, language: str = "fa") -> str:
    if not value:
        return "نامشخص" if language != "en" else "Unknown"

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return format_date_for_language(parsed.astimezone(DISPLAY_TIMEZONE), language)
    except (TypeError, ValueError):
        return str(value)


def _payment_error_message(exc: BackendAPIError, language: str = "fa") -> str:
    if language == "en":
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else None
        if code == "pending_payment_exists":
            payment_id = detail.get("payment_id", "?")
            return (
                "⏳ You already have a payment receipt under review.\n\n"
                f"Payment ID: <code>{payment_id}</code>\n"
                "You can submit a new request after it is reviewed."
            )
        if code == "duplicate_receipt":
            return "❌ This receipt has already been submitted.\n\nPlease send a new receipt."
        if code == "maintenance_mode":
            return "🛠 The bot is temporarily under maintenance.\n\nPlease try again later."
        if code == "payments_disabled":
            return "⏸ Subscription purchases are temporarily disabled.\n\nPlease try again later."
        if exc.status_code == 503:
            return "⚙️ USDT/payment destinations are not fully configured yet.\n\nPlease contact support."
        if exc.status_code in {404, 409}:
            return "🔄 The plan or payment destination changed.\n\nCancel this purchase and choose the plan again."
        return "❌ The payment operation could not be completed.\n\nPlease try again later."

    detail = exc.detail

    if isinstance(detail, dict):
        code = detail.get("code")

        if code == "pending_payment_exists":
            payment_id = detail.get("payment_id", "?")
            return (
                "⏳ یک رسید در حال بررسی دارید.\n\n"
                f"شناسه پرداخت: <code>{payment_id}</code>\n"
                "پس از بررسی مدیر می‌توانید درخواست جدید ثبت کنید."
            )

        if code == "duplicate_receipt":
            return (
                "❌ این فایل رسید قبلاً ثبت شده است.\n\n"
                "لطفاً تصویر یا فایل رسید جدید را ارسال کنید."
            )

        if code == "maintenance_mode":
            return (
                "🛠 ربات موقتاً در حالت تعمیرات است.\n\n"
                "لطفاً کمی بعد دوباره تلاش کنید."
            )

        if code == "payments_disabled":
            return (
                "⏸ خرید اشتراک موقتاً غیرفعال است.\n\n"
                "لطفاً کمی بعد دوباره تلاش کنید."
            )

    if exc.status_code == 503:
        return (
            "⚙️ سیستم پرداخت هنوز به‌طور کامل تنظیم نشده است.\n\n"
            "لطفاً با پشتیبانی تماس بگیرید."
        )

    if exc.status_code in {404, 409}:
        return (
            "🔄 اطلاعات پلن یا کارت پرداخت تغییر کرده است.\n\n"
            "لطفاً خرید را لغو کنید و دوباره پلن را انتخاب کنید."
        )

    return (
        "❌ انجام عملیات پرداخت ممکن نشد.\n\n"
        f"<code>{html.escape(str(detail)[:500])}</code>"
    )


def _build_admin_caption(result: dict, offer: dict) -> str:
    payment = result["payment"]
    user = result["user"]
    full_name = " ".join(
        value
        for value in [user.get("first_name"), user.get("last_name")]
        if value
    ) or "—"
    username = user.get("username")
    username_text = f"@{username}" if username else "—"
    currency = str(offer.get("currency") or "IRT")
    amount_text = (
        format_usdt(payment["amount"])
        if currency == "USDT"
        else format_toman(payment["amount"])
    )
    destination = payment.get("payment_destination_snapshot") or {}
    destination_text = ""
    if currency == "USDT":
        destination_text = (
            "\n🌐 شبکه: "
            f"<code>{html.escape(str(destination.get('network_code') or '—'))}</code>"
            "\n📬 مقصد: "
            f"<code>{html.escape(str(destination.get('address') or '—'))}</code>"
        )

    return (
        "🧾 <b>رسید جدید خرید اشتراک</b>\n\n"
        f"🆔 شناسه پرداخت: <code>{payment['id']}</code>\n"
        f"👤 نام: {html.escape(full_name)}\n"
        f"🔗 نام کاربری: {html.escape(username_text)}\n"
        f"📱 Telegram ID: <code>{user['telegram_id']}</code>\n\n"
        f"💎 بسته: <b>{html.escape(offer['label'])}</b>\n"
        f"💰 مبلغ: <b>{amount_text}</b>{destination_text}\n"
        f"📅 مدت: <code>{payment['duration_days']} روز</code>\n"
        f"📎 نوع رسید: <code>{payment['receipt_file_type']}</code>\n\n"
        "⏳ وضعیت: <b>در انتظار بررسی</b>"
    )


def _offer_details_text(offer: dict, language: str) -> str:
    is_fa = language != "en"
    duration = (
        f"{int(offer.get('duration_days') or 0)} روز"
        if is_fa else f"{int(offer.get('duration_days') or 0)} days"
    )
    limit = offer.get("daily_download_limit")
    limit_text = (
        "نامحدود" if is_fa and limit is None else
        "Unlimited" if limit is None else str(limit)
    )
    quality = (
        "نامحدود" if is_fa and offer.get("max_quality") is None else
        "Unlimited" if offer.get("max_quality") is None else
        format_quality_limit(int(offer["max_quality"]))
    )
    max_file = offer.get("max_file_size_mb")
    max_file_text = "نامحدود" if is_fa and max_file is None else "Unlimited" if max_file is None else f"{max_file} MB"
    concurrency = int(offer.get("max_concurrent_downloads") or 1)
    description = html.escape(str(offer.get("description") or ("توضیحی برای این پلن ثبت نشده است." if is_fa else "No description provided.")))
    if is_fa:
        return (
            f"💎 <b>{html.escape(str(offer.get('label') or '—'))}</b>\n\n"
            f"📝 {description}\n\n"
            f"📅 مدت: <code>{duration}</code>\n"
            f"📥 سقف دانلود روزانه: <code>{limit_text}</code>\n"
            f"📦 حداکثر حجم هر فایل: <code>{max_file_text}</code>\n"
            f"🎞 حداکثر کیفیت: <code>{quality}</code>\n"
            f"⚙️ دانلود هم‌زمان: <code>{concurrency}</code>\n"
            f"🚀 پردازش با اولویت: <code>{'بله' if offer.get('priority_processing') else 'خیر'}</code>\n"
            f"📣 عضویت اجباری: <code>{'بله' if offer.get('forced_join_required') else 'خیر'}</code>\n\n"
            f"💰 مبلغ: <b>{format_toman(offer.get('price'))}</b>"
        )
    return (
        f"💎 <b>{html.escape(str(offer.get('label') or '—'))}</b>\n\n"
        f"📝 {description}\n\n"
        f"📅 Duration: <code>{duration}</code>\n"
        f"📥 Daily downloads: <code>{limit_text}</code>\n"
        f"📦 Maximum file size: <code>{max_file_text}</code>\n"
        f"🎞 Maximum quality: <code>{quality}</code>\n"
        f"⚙️ Concurrent downloads: <code>{concurrency}</code>\n"
        f"🚀 Priority processing: <code>{'Yes' if offer.get('priority_processing') else 'No'}</code>\n"
        f"📣 Required membership: <code>{'Yes' if offer.get('forced_join_required') else 'No'}</code>\n\n"
        f"💰 Amount: <b>{format_usdt(offer.get('price'))}</b>"
    )


def _payment_destination_text(offer: dict, destination: dict, receipt_rules: dict, language: str) -> str:
    currency = str(offer.get("currency") or "IRT")
    if currency == "USDT":
        network = format_usdt_network(destination)
        return (
            f"💎 <b>{html.escape(str(offer['label']))}</b>\n"
            f"📅 Duration: <code>{int(offer['duration_days'])} days</code>\n"
            f"💰 Amount: <b>{format_usdt(offer['price'])}</b>\n\n"
            "Send the exact amount of USDT to this address:\n\n"
            "🌐 Network: "
            f"<b>{html.escape(network)}</b>\n"
            "💵 Asset: "
            f"<code>{html.escape(str(destination.get('asset_symbol') or 'USDT'))}</code>\n"
            "📬 Address: "
            f"<code>{html.escape(str(destination.get('address') or '—'))}</code>\n\n"
            "📷 Scan the QR code or copy the address exactly.\n\n"
            "⚠️ Use only the displayed network; transfers on another network may be lost.\n\n"
            "📎 Then send a screenshot or PDF receipt here.\n"
            "Maximum receipt size: "
            f"<code>{receipt_rules['max_size_mb']} MB</code>"
        )
    bank_line = ""
    if destination.get("bank_name"):
        bank_line = f"\n🏦 بانک: <b>{html.escape(str(destination['bank_name']))}</b>"
    return (
        f"💎 <b>{html.escape(str(offer['label']))}</b>\n"
        f"📅 مدت: <code>{int(offer['duration_days'])} روز</code>\n"
        f"💰 مبلغ: <b>{format_toman(offer['price'])}</b>\n\n"
        "لطفاً مبلغ را به کارت زیر واریز کنید:\n\n"
        f"💳 <code>{html.escape(str(destination['card_number']))}</code>\n"
        "👤 به نام: "
        f"<b>{html.escape(str(destination['card_holder']))}</b>"
        f"{bank_line}\n\n"
        "📎 سپس تصویر رسید یا فایل PDF را همین‌جا ارسال کنید.\n"
        "حداکثر حجم رسید: "
        f"<code>{receipt_rules['max_size_mb']} MB</code>"
    )


def _status_caption(original: str | None, status_line: str) -> str:
    base = (original or "🧾 رسید پرداخت").strip()
    marker = "\n\n⏳ وضعیت:"

    if marker in base:
        base = base.split(marker, 1)[0]

    max_base_length = max(0, 1024 - len(status_line) - 4)
    return f"{base[:max_base_length]}\n\n{status_line}"


async def _edit_receipt_status(
    message: Message,
    status_line: str,
) -> None:
    caption = _status_caption(message.caption, status_line)

    try:
        await message.edit_caption(
            caption=caption,
            parse_mode="HTML",
            reply_markup=None,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _edit_receipt_status_by_id(
    message: Message,
    *,
    chat_id: int,
    message_id: int,
    original_caption: str | None,
    status_line: str,
) -> None:
    caption = _status_caption(original_caption, status_line)

    try:
        await message.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=None,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _notify_user_approved(message: Message, result: dict) -> None:
    user = result["user"]
    payment = result["payment"]
    subscription = result.get("subscription") or {}
    language = normalize_language(user.get("effective_language"))
    if language == "en":
        text = (
            "✅ <b>Your payment was approved</b>\n\n"
            f"🆔 Payment ID: <code>{payment['id']}</code>\n"
            f"💎 Plan: <b>{html.escape(str(payment['plan_name_snapshot']))}</b>\n"
            f"💎 Added duration: <code>{payment['duration_days']} days</code>\n"
            "📅 Valid until: "
            f"<code>{_format_datetime(subscription.get('expires_at'), language)}</code>"
        )
    else:
        text = (
            "✅ <b>پرداخت شما تأیید شد</b>\n\n"
            f"🆔 شناسه پرداخت: <code>{payment['id']}</code>\n"
            "💎 پلن: "
            f"<b>{html.escape(str(payment['plan_name_snapshot']))}</b>\n"
            f"💎 مدت افزوده‌شده: <code>{payment['duration_days']} روز</code>\n"
            "📅 اعتبار اشتراک تا: "
            f"<code>{_format_datetime(subscription.get('expires_at'), language)}</code>"
        )

    await message.bot.send_message(
        chat_id=user["telegram_id"],
        text=text,
        parse_mode="HTML",
        reply_markup=await _user_home_reply_keyboard(user),
    )


async def _notify_user_rejected(message: Message, result: dict) -> None:
    user = result["user"]
    payment = result["payment"]
    language = normalize_language(user.get("effective_language"))
    reason = payment.get("rejection_reason") or ("Receipt was not approved" if language == "en" else "رسید تأیید نشد")

    if language == "en":
        reason = "The administrator did not approve this receipt."
        text = (
            "❌ <b>Your payment receipt was not approved</b>\n\n"
            f"Payment ID: <code>{payment['id']}</code>\n"
            f"Reason: {html.escape(str(reason))}\n\n"
            "After fixing the issue, you can submit a new receipt."
        )
    else:
        text = (
            "❌ <b>رسید پرداخت شما تأیید نشد</b>\n\n"
            f"🆔 شناسه پرداخت: <code>{payment['id']}</code>\n"
            f"📝 دلیل: {html.escape(str(reason))}\n\n"
            "می‌توانید پس از رفع مشکل، رسید جدیدی ثبت کنید."
        )

    await message.bot.send_message(
        chat_id=user["telegram_id"],
        text=text,
        parse_mode="HTML",
        reply_markup=await _user_home_reply_keyboard(user),
    )


async def send_payment_offers_menu(
    message: Message,
    state: FSMContext,
) -> None:
    """Open subscription offers from the persistent reply keyboard."""
    language = "fa"
    try:
        user = (
            await get_telegram_user(message.from_user.id)
            if message.from_user is not None
            else {}
        )
        language = normalize_language(user.get("effective_language"))
        configuration = await get_payment_configuration(
            select_destination=False,
            language=language,
        )
        await state.clear()
        await message.answer(
            (
                "💎 <b>خرید اشتراک</b>\n\nپلن موردنظر را انتخاب کنید:"
                if language == "fa"
                else "💎 <b>Buy subscription</b>\n\nChoose a plan:"
            ),
            parse_mode="HTML",
            reply_markup=build_payment_offers_keyboard(
                configuration["offers"],
                language,
            ),
        )
    except BackendAPIError as exc:
        await message.answer(
            _payment_error_message(exc, language),
            parse_mode="HTML",
        )


def _subscription_status_text(result: dict, language: str = "fa") -> str:
    is_fa = language == "fa"
    if not result.get("is_active"):
        return ("👤 <b>وضعیت اشتراک</b>\n\n" if is_fa else "👤 <b>My subscription</b>\n\n") + (
            "در حال حاضر اشتراک فعالی ندارید." if is_fa else "You do not have an active subscription."
        )

    plan_name = html.escape(
        str(
            (
                result.get("plan_name")
                if is_fa
                else result.get("plan_name_en")
            )
            or (result.get("plan_name") if is_fa else None)
            or "—"
        )
    )
    duration_days = int(result.get("duration_days") or 0)
    duration_labels = {
        30: ("یک‌ماهه", "1 month"),
        90: ("سه‌ماهه", "3 months"),
        180: ("شش‌ماهه", "6 months"),
        365: ("یک‌ساله", "1 year"),
    }
    duration = duration_labels.get(duration_days, (f"{duration_days} روز", f"{duration_days} days"))[0 if is_fa else 1]
    try:
        expiry = datetime.fromisoformat(str(result["expires_at"]).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=DISPLAY_TIMEZONE)
        remaining_days = max(0, int((expiry - datetime.now(expiry.tzinfo)).total_seconds() + 86399) // 86400)
    except (KeyError, TypeError, ValueError):
        remaining_days = 0
    limit = result.get("daily_download_limit")
    limit_text = "♾️ نامحدود" if is_fa and limit is None else ("♾️ Unlimited" if limit is None else str(limit))
    remaining = "♾️ نامحدود" if is_fa and result.get("remaining_downloads") is None else ("♾️ Unlimited" if result.get("remaining_downloads") is None else str(result.get("remaining_downloads")))
    labels = ("مدت اشتراک", "Subscription duration", "تاریخ عضویت", "Registered", "تعداد دانلودهای انجام‌شده", "Downloads completed", "محدودیت دانلود روزانه", "Daily download limit", "دانلود باقیمانده", "Downloads remaining", "روز باقی‌مانده", "Days remaining")
    return (
        ("👤 <b>وضعیت اشتراک</b>\n\n" if is_fa else "👤 <b>My subscription</b>\n\n")
        + ("✅ اشتراک شما فعال است.\n" if is_fa else "✅ Your subscription is active.\n")
        + f"💎 {'پلن' if is_fa else 'Plan'}: <b>{plan_name}</b>\n"
        + f"📦 {labels[0 if is_fa else 1]}: <code>{html.escape(duration)}</code>\n"
        + f"📅 {'اعتبار تا' if is_fa else 'Valid until'}: <code>{_format_datetime(result.get('expires_at'), language)}</code>\n"
        + f"⏳ {labels[10 if is_fa else 11]}: <code>{remaining_days}</code>\n"
        + f"🗓️ {labels[2 if is_fa else 3]}: <code>{_format_datetime(result.get('registered_at'), language)}</code>\n"
        + f"📥 {labels[4 if is_fa else 5]}: <code>{int(result.get('downloads_done') or 0)}</code>\n"
        + f"📊 {labels[6 if is_fa else 7]}: <code>{limit_text}</code>\n"
        + f"✅ {labels[8 if is_fa else 9]}: <code>{remaining}</code>"
    )


async def send_subscription_status(
    message: Message,
    telegram_id: int,
) -> None:
    """Show subscription status from the persistent reply keyboard."""
    language = "fa"
    try:
        result = await get_current_subscription(telegram_id)
        user = await get_telegram_user(telegram_id)
        language = normalize_language(user.get("effective_language"))
        await message.answer(
            _subscription_status_text(result, language),
            parse_mode="HTML",
            reply_markup=await _user_home_reply_keyboard(user),
        )
    except BackendAPIError:
        await message.answer("Could not load subscription status." if language == "en" else "دریافت وضعیت اشتراک ممکن نشد.")


@router.callback_query(F.data == "payment:open")
async def open_payment_offers(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    language = "fa"
    try:
        user = await get_telegram_user(callback.from_user.id)
        language = normalize_language(user.get("effective_language"))
        configuration = await get_payment_configuration(
            select_destination=False,
            language=language,
        )
        await state.clear()
        await callback.message.edit_text(
            (
                "💎 <b>خرید اشتراک</b>\n\nپلن موردنظر را انتخاب کنید:"
                if language == "fa"
                else "💎 <b>Buy subscription</b>\n\nChoose a plan:"
            ),
            parse_mode="HTML",
            reply_markup=build_payment_offers_keyboard(
                configuration["offers"],
                language,
            ),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(
            "Payment system is not ready." if language == "en" else "سیستم پرداخت آماده نیست.",
            show_alert=True,
        )
        await callback.message.answer(
            _payment_error_message(exc, language),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "payment:status")
async def payment_status(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return

    language = "fa"
    try:
        result = await get_current_subscription(callback.from_user.id)
        user = await get_telegram_user(callback.from_user.id)
        language = normalize_language(user.get("effective_language"))

        await callback.message.edit_text(
            _subscription_status_text(result, language),
            parse_mode="HTML",
            reply_markup=await _user_home_inline_keyboard(callback.from_user.id),
        )
        await callback.answer()
    except BackendAPIError:
        await callback.answer(
            "Could not load subscription status." if language == "en" else "دریافت وضعیت اشتراک ممکن نشد.",
            show_alert=True,
        )


@router.callback_query(F.data == "payment:offer:continue")
async def continue_payment_offer(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    language = "fa"
    try:
        user = await get_telegram_user(callback.from_user.id)
        language = normalize_language(user.get("effective_language"))
        data = await state.get_data()
        offer = data.get("offer")
        if not isinstance(offer, dict):
            await callback.answer(
                "Purchase expired; choose a plan again." if language == "en" else "درخواست خرید منقضی شده است.",
                show_alert=True,
            )
            return
        configuration = await get_payment_configuration(
            select_destination=True,
            language=language,
        )
        selected = _find_offer(configuration, str(data.get("offer_code") or offer.get("code")))
        if selected is None:
            raise BackendAPIError(status_code=404, detail={"code": "plan_not_found"})
        currency = str(selected.get("currency") or "IRT")

        if currency == "USDT":
            destinations = configuration.get("destinations") or []
            if not destinations:
                raise BackendAPIError(
                    status_code=503,
                    detail="No active USDT destination is configured",
                )
            await state.set_state(PaymentStates.selecting_usdt_destination)
            await state.update_data(
                offer=selected,
                offer_code=selected["code"],
                payment_card_id=None,
                usdt_destination_id=None,
                currency=currency,
                receipt_rules=configuration["receipt"],
            )
            await callback.message.edit_text(
                "🌐 <b>Choose the USDT transfer network</b>\n\n"
                "The address and QR code shown next will belong to the "
                "network you select.",
                parse_mode="HTML",
                reply_markup=build_usdt_destination_keyboard(destinations),
            )
            await callback.answer()
            return

        destination = configuration["destination"]
        await state.set_state(PaymentStates.waiting_for_receipt)
        await state.update_data(
            offer=selected,
            offer_code=selected["code"],
            payment_card_id=destination.get("id"),
            usdt_destination_id=None,
            currency=currency,
            receipt_rules=configuration["receipt"],
        )
        destination_text = _payment_destination_text(
            selected,
            destination,
            configuration["receipt"],
            language,
        )
        destination_keyboard = build_receipt_cancel_keyboard(language)
        await callback.message.edit_text(
            destination_text,
            parse_mode="HTML",
            reply_markup=destination_keyboard,
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(
            "Payment system is not ready." if language == "en" else "سیستم پرداخت آماده نیست.",
            show_alert=True,
        )
        await callback.message.answer(_payment_error_message(exc, language), parse_mode="HTML")


@router.callback_query(
    PaymentStates.selecting_usdt_destination,
    F.data.startswith("payment:usdt-destination:"),
)
async def select_usdt_destination(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    try:
        destination_id = int(callback.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        await callback.answer("Invalid network selection.", show_alert=True)
        return

    language = "en"
    try:
        user = await get_telegram_user(callback.from_user.id)
        language = normalize_language(user.get("effective_language"))
        if language != "en":
            await callback.answer("این روش پرداخت فقط برای زبان انگلیسی است.", show_alert=True)
            return

        data = await state.get_data()
        offer_code = str(data.get("offer_code") or "")
        if not offer_code:
            await callback.answer(
                "Purchase expired; choose a plan again.",
                show_alert=True,
            )
            return

        configuration = await get_payment_configuration(
            select_destination=True,
            language="en",
        )
        selected = _find_offer(configuration, offer_code)
        destination = next(
            (
                item
                for item in (configuration.get("destinations") or [])
                if int(item.get("id") or 0) == destination_id
            ),
            None,
        )
        if selected is None or destination is None:
            raise BackendAPIError(
                status_code=409,
                detail="The selected USDT network is no longer available",
            )

        receipt_rules = configuration["receipt"]
        await state.set_state(PaymentStates.waiting_for_receipt)
        await state.update_data(
            offer=selected,
            offer_code=selected["code"],
            payment_card_id=None,
            usdt_destination_id=destination_id,
            currency="USDT",
            receipt_rules=receipt_rules,
        )
        destination_text = _payment_destination_text(
            selected,
            destination,
            receipt_rules,
            "en",
        )
        destination_keyboard = build_receipt_cancel_keyboard("en")
        try:
            qr_png = build_usdt_address_qr(str(destination.get("address") or ""))
            await callback.message.answer_photo(
                photo=BufferedInputFile(
                    qr_png,
                    filename="usdt-deposit-address.png",
                ),
                caption=destination_text,
                parse_mode="HTML",
                reply_markup=destination_keyboard,
            )
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
        except Exception:
            logger.exception("Could not generate or send the USDT address QR")
            await callback.message.edit_text(
                destination_text,
                parse_mode="HTML",
                reply_markup=destination_keyboard,
            )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(
            "The selected network is not available. Choose again."
            if language == "en"
            else "شبکه انتخابی در دسترس نیست.",
            show_alert=True,
        )
        await callback.message.answer(_payment_error_message(exc, language), parse_mode="HTML")


@router.callback_query(F.data.startswith("payment:offer:"))
async def select_payment_offer(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    offer_code = callback.data.rsplit(":", 1)[-1]

    language = "fa"
    try:
        user = await get_telegram_user(callback.from_user.id)
        language = normalize_language(user.get("effective_language"))
        configuration = await get_payment_configuration(
            select_destination=False,
            language=language,
        )
        offer = _find_offer(configuration, offer_code)

        if offer is None:
            await callback.answer(
                "The selected plan is not available." if language == "en" else "بسته انتخاب‌شده معتبر نیست.",
                show_alert=True,
            )
            return
        await state.set_state(PaymentStates.confirming_offer)
        await state.update_data(
            offer=offer,
            offer_code=offer_code,
        )
        await callback.message.edit_text(
            _offer_details_text(offer, language),
            parse_mode="HTML",
            reply_markup=build_payment_offer_detail_keyboard(language),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(
            "Payment system is not ready." if language == "en" else "سیستم پرداخت آماده نیست.",
            show_alert=True,
        )
        await callback.message.answer(
            _payment_error_message(exc, language),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "payment:cancel")
async def cancel_payment_flow(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()

    language = "fa"
    try:
        user = await get_telegram_user(callback.from_user.id)
        language = normalize_language(user.get("effective_language"))
    except BackendAPIError:
        pass
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Subscription purchase cancelled." if language == "en" else "خرید اشتراک لغو شد.",
            reply_markup=await _user_home_inline_keyboard(callback.from_user.id),
        )

    await callback.answer("Cancelled." if language == "en" else "لغو شد.")


@router.message(PaymentStates.waiting_for_receipt, F.photo | F.document)
async def receive_payment_receipt(
    message: Message,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return

    language = "fa"
    try:
        language = normalize_language(
            (await get_telegram_user(message.from_user.id)).get("effective_language")
        )
    except BackendAPIError:
        pass
    state_data = await state.get_data()
    offer_code = state_data.get("offer_code")
    offer = state_data.get("offer")
    receipt_rules = state_data.get("receipt_rules")
    payment_card_id = state_data.get("payment_card_id")
    usdt_destination_id = state_data.get("usdt_destination_id")
    currency = str(state_data.get("currency") or "IRT")

    if not offer_code or not isinstance(offer, dict):
        await state.clear()
        await message.answer(
            "This purchase expired; choose a plan again."
            if language == "en"
            else "درخواست خرید منقضی شده است؛ دوباره پلن را انتخاب کنید."
        )
        return

    try:
        if ADMIN_PAYMENT_CHAT_ID is None or ADMIN_PAYMENT_TOPIC_ID is None:
            raise RuntimeError("Admin payments topic is not configured")

        if message.photo:
            receipt = message.photo[-1]
            file_id = receipt.file_id
            unique_id = receipt.file_unique_id
            file_size = receipt.file_size
            file_type = "photo"
            mime_type = "image/jpeg"
            file_name = None
        else:
            document = message.document

            if document is None:
                return

            file_id = document.file_id
            unique_id = document.file_unique_id
            file_size = document.file_size
            file_type = "document"
            mime_type = (document.mime_type or "").lower()
            file_name = document.file_name

            allowed_types = set((receipt_rules or {}).get("allowed_types", []))
            if mime_type not in allowed_types:
                await message.answer(
                    "❌ Invalid receipt format. Send an image or PDF only."
                    if language == "en" else "❌ فرمت رسید مجاز نیست. فقط تصویر یا PDF ارسال کنید."
                )
                return

        max_size_mb = int((receipt_rules or {}).get("max_size_mb", 10))
        if file_size is not None and file_size > max_size_mb * 1024 * 1024:
            await message.answer(
                f"❌ Receipt is larger than {max_size_mb} MB."
                if language == "en" else f"❌ حجم رسید بیشتر از {max_size_mb} مگابایت است."
            )
            return

        await register_telegram_user(message)
        result = await create_manual_payment(
            telegram_id=message.from_user.id,
            offer_code=offer_code,
            receipt_file_id=file_id,
            receipt_file_unique_id=unique_id,
            receipt_file_type=file_type,
            receipt_file_size=file_size,
            receipt_mime_type=mime_type,
            receipt_file_name=file_name,
            user_receipt_message_id=message.message_id,
            payment_card_id=(
                int(payment_card_id) if payment_card_id is not None else None
            ),
            currency=currency,
            usdt_destination_id=(
                int(usdt_destination_id)
                if usdt_destination_id is not None
                else None
            ),
        )
        payment_id = int(result["payment"]["id"])
        caption = _build_admin_caption(result, offer)
        send_kwargs = {
            "chat_id": ADMIN_PAYMENT_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML",
            "message_thread_id": ADMIN_PAYMENT_TOPIC_ID,
            "reply_markup": build_admin_payment_keyboard(payment_id),
        }

        try:
            if file_type == "photo":
                admin_message = await message.bot.send_photo(
                    photo=file_id,
                    **send_kwargs,
                )
            else:
                admin_message = await message.bot.send_document(
                    document=file_id,
                    **send_kwargs,
                )
        except Exception:
            try:
                await mark_payment_delivery_failed(payment_id)
            except Exception:
                pass
            raise

        try:
            await set_payment_admin_message(
                payment_id=payment_id,
                admin_chat_id=ADMIN_PAYMENT_CHAT_ID,
                admin_message_id=admin_message.message_id,
                admin_message_thread_id=ADMIN_PAYMENT_TOPIC_ID,
            )
        except Exception as exc:
            print(
                "Failed to store admin payment message metadata: "
                f"{type(exc).__name__}: {exc}"
            )

        await state.clear()
        await message.answer(
            (
                "✅ <b>Your receipt was submitted</b>\n\n"
                f"Payment ID: <code>{payment_id}</code>\n"
                "You will be notified here after administrator review."
                if language == "en" else
                "✅ <b>رسید شما ثبت شد</b>\n\n"
                f"🆔 شناسه پرداخت: <code>{payment_id}</code>\n"
                "پس از بررسی مدیر، نتیجه همین‌جا اطلاع داده می‌شود."
            ),
            parse_mode="HTML",
            reply_markup=await _user_home_reply_keyboard(result["user"]),
        )
    except BackendAPIError as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "pending_payment_exists":
            await state.clear()

        await message.answer(
            _payment_error_message(exc, language),
            parse_mode="HTML",
        )
    except Exception as exc:
        await message.answer(
            (
                "❌ We could not send the receipt to the finance team.\n\n"
                "Please try again later."
                if language == "en" else
                "❌ ارسال رسید به بخش مالی انجام نشد.\n\n"
                "لطفاً کمی بعد دوباره تلاش کنید.\n"
                f"<code>{html.escape(str(exc)[:300])}</code>"
            ),
            parse_mode="HTML",
        )


@router.message(PaymentStates.waiting_for_receipt)
async def invalid_payment_receipt(message: Message) -> None:
    language = "fa"
    if message.from_user is not None:
        try:
            language = normalize_language((await get_telegram_user(message.from_user.id)).get("effective_language"))
        except BackendAPIError:
            pass
    await message.answer(
        "📎 Send a receipt image or PDF file only."
        if language == "en" else "📎 لطفاً فقط تصویر رسید یا فایل PDF را ارسال کنید."
    )


def _parse_admin_payment_id(callback: CallbackQuery) -> int | None:
    if not callback.data:
        return None

    try:
        return int(callback.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None


async def _ensure_admin(callback: CallbackQuery) -> bool:
    try:
        context = await get_admin_context(callback.from_user.id)
        permissions = set(context.get("permissions", []))

        if context.get("is_superadmin") or "payments.review" in permissions:
            return True
    except BackendAPIError:
        await callback.answer(
            "بررسی دسترسی مدیر ممکن نشد.",
            show_alert=True,
        )
        return False

    await callback.answer("شما دسترسی مدیر ندارید.", show_alert=True)
    return False


async def _ensure_message_admin(message: Message) -> bool:
    if message.from_user is None:
        return False

    try:
        context = await get_admin_context(message.from_user.id)
        permissions = set(context.get("permissions", []))
        return bool(
            context.get("is_superadmin")
            or "payments.review" in permissions
        )
    except BackendAPIError:
        return False


@router.callback_query(F.data.startswith("payment_admin:approve:"))
async def approve_payment_callback(callback: CallbackQuery) -> None:
    if not await _ensure_admin(callback):
        return

    payment_id = _parse_admin_payment_id(callback)
    if payment_id is None or not isinstance(callback.message, Message):
        await callback.answer("شناسه پرداخت نامعتبر است.", show_alert=True)
        return

    try:
        result = await approve_manual_payment(
            payment_id=payment_id,
            admin_telegram_id=callback.from_user.id,
        )
        subscription = result.get("subscription") or {}
        status_line = (
            "✅ وضعیت: <b>تأیید شد</b>\n"
            f"👮 مدیر: <code>{callback.from_user.id}</code>\n"
            "📅 اعتبار تا: "
            f"<code>{_format_datetime(subscription.get('expires_at'))}</code>"
        )
        await _edit_receipt_status(callback.message, status_line)

        if not result.get("already_reviewed"):
            try:
                await _notify_user_approved(callback.message, result)
            except Exception as exc:
                await callback.message.reply(
                    "⚠️ پرداخت تأیید شد، اما پیام نتیجه به کاربر نرسید: "
                    f"<code>{html.escape(str(exc)[:250])}</code>",
                    parse_mode="HTML",
                )

        callback_text = (
            "این پرداخت قبلاً تأیید شده بود."
            if result.get("already_reviewed")
            else "پرداخت تأیید و اشتراک تمدید شد."
        )
        await callback.answer(callback_text)
    except BackendAPIError as exc:
        await callback.answer(str(exc.detail)[:180], show_alert=True)


@router.callback_query(F.data.startswith("payment_admin:reject:"))
async def request_rejection_reason(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not await _ensure_admin(callback):
        return

    payment_id = _parse_admin_payment_id(callback)
    if payment_id is None or not isinstance(callback.message, Message):
        await callback.answer("شناسه پرداخت نامعتبر است.", show_alert=True)
        return

    await state.set_state(AdminPaymentStates.waiting_for_rejection_reason)
    await state.update_data(
        payment_id=payment_id,
        receipt_chat_id=callback.message.chat.id,
        receipt_message_id=callback.message.message_id,
        receipt_caption=callback.message.caption,
    )
    await callback.message.reply(
        (
            f"📝 دلیل رد پرداخت <code>#{payment_id}</code> را ارسال کنید.\n"
            "برای انصراف از <code>/cancel</code> استفاده کنید."
        ),
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer("دلیل رد را ارسال کنید.")


@router.message(AdminPaymentStates.waiting_for_rejection_reason, F.text)
async def receive_rejection_reason(
    message: Message,
    state: FSMContext,
) -> None:
    if not await _ensure_message_admin(message):
        await state.clear()
        await message.answer("شما دسترسی بررسی پرداخت‌ها را ندارید.")
        return

    reason = (message.text or "").strip()

    if reason.lower() == "/cancel":
        await state.clear()
        await message.answer("رد پرداخت لغو شد.")
        return

    if len(reason) < 2:
        await message.answer("دلیل رد باید حداقل دو نویسه باشد.")
        return

    state_data = await state.get_data()
    payment_id = int(state_data["payment_id"])

    try:
        result = await reject_manual_payment(
            payment_id=payment_id,
            admin_telegram_id=message.from_user.id,
            reason=reason,
        )
        actual_reason = (
            result["payment"].get("rejection_reason")
            or reason
        )
        await _edit_receipt_status_by_id(
            message,
            chat_id=int(state_data["receipt_chat_id"]),
            message_id=int(state_data["receipt_message_id"]),
            original_caption=state_data.get("receipt_caption"),
            status_line=(
                "❌ وضعیت: <b>رد شد</b>\n"
                f"👮 مدیر: <code>{message.from_user.id}</code>\n"
                f"📝 دلیل: {html.escape(actual_reason)}"
            ),
        )

        if not result.get("already_reviewed"):
            try:
                await _notify_user_rejected(message, result)
            except Exception as exc:
                await message.answer(
                    "⚠️ پرداخت رد شد، اما پیام نتیجه به کاربر نرسید: "
                    f"<code>{html.escape(str(exc)[:250])}</code>",
                    parse_mode="HTML",
                )

        await state.clear()
        result_text = (
            f"پرداخت #{payment_id} قبلاً رد شده بود."
            if result.get("already_reviewed")
            else f"❌ پرداخت #{payment_id} رد شد."
        )
        await message.answer(result_text)
    except BackendAPIError as exc:
        await message.answer(
            f"❌ <code>{html.escape(str(exc.detail)[:500])}</code>",
            parse_mode="HTML",
        )
