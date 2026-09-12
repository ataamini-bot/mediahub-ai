from app.localization import tr as _tr, localized_collection as _localized_collection
import html
import logging
import os
import re
from urllib.parse import quote
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from app.services.topic_delivery import notification_routes
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, ForceReply, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app.keyboards.payment import (
    build_admin_payment_keyboard,
    build_payment_approval_confirmation_keyboard,
    build_home_keyboard,
    build_home_reply_keyboard,
    build_payment_offer_detail_keyboard,
    build_payment_offers_keyboard,
    build_receipt_cancel_keyboard,
    build_usdt_destination_keyboard,
    build_usdt_screenshot_keyboard,
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


async def _replace_payment_message(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    """Replace a payment screen whether its current message is text or media."""
    if message.text is not None:
        await message.edit_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return

    await message.answer(
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    try:
        await message.delete()
    except TelegramBadRequest:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


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
        return _tr("نامشخص") if language != "en" else "Unknown"

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
        if code == "duplicate_txid":
            return "❌ This TxID has already been submitted on this network."
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
                f"{_tr('⏳ یک رسید در حال بررسی دارید.\n\nشناسه پرداخت: <code>')}{payment_id}{_tr('</code>\nپس از بررسی مدیر می\u200cتوانید درخواست جدید ثبت کنید.')}"
            )

        if code == "duplicate_receipt":
            return (
                _tr("❌ این فایل رسید قبلاً ثبت شده است.\n\n"
                "لطفاً تصویر یا فایل رسید جدید را ارسال کنید.")
            )

        if code == "maintenance_mode":
            return (
                _tr("🛠 ربات موقتاً در حالت تعمیرات است.\n\n"
                "لطفاً کمی بعد دوباره تلاش کنید.")
            )

        if code == "payments_disabled":
            return (
                _tr("⏸ خرید اشتراک موقتاً غیرفعال است.\n\n"
                "لطفاً کمی بعد دوباره تلاش کنید.")
            )

    if exc.status_code == 503:
        return (
            _tr("⚙️ سیستم پرداخت هنوز به‌طور کامل تنظیم نشده است.\n\n"
            "لطفاً با پشتیبانی تماس بگیرید.")
        )

    if exc.status_code in {404, 409}:
        return (
            _tr("🔄 اطلاعات پلن یا کارت پرداخت تغییر کرده است.\n\n"
            "لطفاً خرید را لغو کنید و دوباره پلن را انتخاب کنید.")
        )

    return (
        f"{_tr('❌ انجام عملیات پرداخت ممکن نشد.\n\n<code>')}{html.escape(str(detail)[:500])}</code>"
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
            f"{_tr('\n🌐 شبکه: <code>')}{html.escape(str(destination.get('network_code') or '—'))}{_tr('</code>\n📬 مقصد: <code>')}{html.escape(str(destination.get('address') or '—'))}</code>\n🔗 TxID: <code>{html.escape(str(payment.get('txid') or '—'))}</code>"
        )

    return (
        f"{_tr('🧾 <b>رسید جدید خرید اشتراک</b>\n\n🆔 شناسه پرداخت: <code>')}{payment['id']}{_tr('</code>\n👤 نام: ')}{html.escape(full_name)}{_tr('\n🔗 نام کاربری: ')}{html.escape(username_text)}\n📱 Telegram ID: <code>{user['telegram_id']}{_tr('</code>\n\n💎 بسته: <b>')}{html.escape(offer['label'])}{_tr('</b>\n💰 مبلغ: <b>')}{amount_text}</b>{destination_text}{_tr('\n📅 مدت: <code>')}{payment['duration_days']}{_tr(' روز</code>\n📎 پیوست: <code>')}{payment.get('receipt_file_type') or _tr('بدون اسکرین\u200cشات')}{_tr('</code>\n\n⏳ وضعیت: <b>در انتظار بررسی</b>')}"
    )


def _transaction_explorer_url(payment: dict) -> str | None:
    txid = str(payment.get("txid") or "").strip()
    if not txid:
        return None
    destination = payment.get("payment_destination_snapshot") or {}
    network = str(destination.get("network_code") or "").upper()
    configured = str(destination.get("explorer_url") or "").strip()
    if configured and not configured.lower().startswith(("https://", "http://")):
        configured = ""
    if "{txid}" in configured:
        return configured.replace("{txid}", quote(txid, safe=""))
    if network in {"TRC20", "TRON"}:
        return f"https://tronscan.org/#/transaction/{quote(txid, safe='')}"
    return configured or None


def _offer_details_text(offer: dict, language: str) -> str:
    is_fa = language != "en"
    duration = (
        f"{int(offer.get('duration_days') or 0)}{_tr(' روز')}"
        if is_fa else f"{int(offer.get('duration_days') or 0)} days"
    )
    limit = offer.get("daily_download_limit")
    limit_text = (
        _tr("نامحدود") if is_fa and limit is None else
        "Unlimited" if limit is None else str(limit)
    )
    quality = (
        _tr("نامحدود") if is_fa and offer.get("max_quality") is None else
        "Unlimited" if offer.get("max_quality") is None else
        format_quality_limit(int(offer["max_quality"]))
    )
    max_file = offer.get("max_file_size_mb")
    max_file_text = _tr("نامحدود") if is_fa and max_file is None else "Unlimited" if max_file is None else f"{max_file} MB"
    concurrency = int(offer.get("max_concurrent_downloads") or 1)
    description = html.escape(str(offer.get("description") or (_tr("توضیحی برای این پلن ثبت نشده است.") if is_fa else "No description provided.")))
    if is_fa:
        text = (
            f"💎 <b>{html.escape(str(offer.get('label') or '—'))}</b>\n\n📝 {description}{_tr('\n\n📅 مدت: <code>')}{duration}{_tr('</code>\n📥 سقف دانلود روزانه: <code>')}{limit_text}{_tr('</code>\n📦 حداکثر حجم هر فایل: <code>')}{max_file_text}{_tr('</code>\n🎞 حداکثر کیفیت: <code>')}{quality}{_tr('</code>\n⚙️ دانلود هم\u200cزمان: <code>')}{concurrency}{_tr('</code>\n🚀 پردازش با اولویت: <code>')}{(_tr('بله') if offer.get('priority_processing') else _tr('خیر'))}{_tr('</code>\n📣 عضویت اجباری: <code>')}{(_tr('بله') if offer.get('forced_join_required') else _tr('خیر'))}{_tr('</code>\n\n💰 مبلغ: <b>')}{format_toman(offer.get('price'))}</b>"
        )
    else:
        text = (
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

    rules = (
        "\n\nPlan changes: renewing the same plan adds duration and daily quota. An upgrade starts after approval and preserves your remaining time. A downgrade or mixed change starts after existing paid subscriptions."
        if language == "en" else
        "\n\nقوانین تغییر پلن: تمدید همان پلن، مدت و سهمیهٔ روزانه را جمع می‌کند. ارتقا پس از تأیید آغاز می‌شود و زمان باقی‌مانده حفظ می‌شود. تنزل یا تغییر ترکیبی پس از پایان اشتراک‌های خریداری‌شده شروع می‌شود."
    )
    return text + rules


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
            "🔗 Then send the transaction ID (TxID).\n"
            "A screenshot is optional and may be attached afterward."
        )
    bank_line = ""
    if destination.get("bank_name"):
        bank_line = f"{_tr('\n🏦 بانک: <b>')}{html.escape(str(destination['bank_name']))}</b>"
    return (
        f"💎 <b>{html.escape(str(offer['label']))}{_tr('</b>\n📅 مدت: <code>')}{int(offer['duration_days'])}{_tr(' روز</code>\n💰 مبلغ: <b>')}{format_toman(offer['price'])}{_tr('</b>\n\nلطفاً مبلغ را به کارت زیر واریز کنید:\n\n💳 <code>')}{html.escape(str(destination['card_number']))}{_tr('</code>\n👤 به نام: <b>')}{html.escape(str(destination['card_holder']))}</b>{bank_line}{_tr('\n\n📎 سپس تصویر رسید یا فایل PDF را همین\u200cجا ارسال کنید.\nحداکثر حجم رسید: <code>')}{receipt_rules['max_size_mb']} MB</code>"
    )


def _status_caption(original: str | None, status_line: str) -> str:
    base = (original or _tr("🧾 رسید پرداخت")).strip()
    marker = _tr("\n\n⏳ وضعیت:")

    if marker in base:
        base = base.split(marker, 1)[0]

    max_base_length = max(0, 1024 - len(status_line) - 4)
    return f"{base[:max_base_length]}\n\n{status_line}"


async def _edit_receipt_status(
    message: Message,
    status_line: str,
) -> None:
    original = message.caption if message.caption is not None else message.text
    caption = _status_caption(original, status_line)

    try:
        if message.caption is not None:
            await message.edit_caption(
                caption=caption,
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await message.edit_text(
                caption,
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
    original_content: str | None,
    is_media: bool,
    status_line: str,
) -> None:
    caption = _status_caption(original_content, status_line)

    try:
        if is_media:
            await message.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=None,
            )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


from app.localization import recipient_language


@recipient_language
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
            f"{_tr('✅ <b>پرداخت شما تأیید شد</b>\n\n🆔 شناسه پرداخت: <code>')}{payment['id']}{_tr('</code>\n💎 پلن: <b>')}{html.escape(str(payment['plan_name_snapshot']))}{_tr('</b>\n💎 مدت افزوده\u200cشده: <code>')}{payment['duration_days']}{_tr(' روز</code>\n📅 اعتبار اشتراک تا: <code>')}{_format_datetime(subscription.get('expires_at'), language)}</code>"
        )

    start_label = "📅 Starts: " if language == "en" else "📅 شروع اشتراک: "
    text += "\n" + start_label + _format_datetime(subscription.get("started_at"), language)
    if payment.get("subscription_change_type") in {"downgrade", "switch"}:
        text += ("\nYour new plan is scheduled after your existing paid subscriptions." if language == "en" else
                 "\nپلن جدید پس از پایان اشتراک‌های خریداری‌شدهٔ قبلی شروع می‌شود.")
    await message.bot.send_message(
        chat_id=user["telegram_id"],
        text=text,
        parse_mode="HTML",
        reply_markup=await _user_home_reply_keyboard(user),
    )


@recipient_language
async def _notify_user_rejected(message: Message, result: dict) -> None:
    user = result["user"]
    payment = result["payment"]
    language = normalize_language(user.get("effective_language"))
    reason = payment.get("rejection_reason") or ("Receipt was not approved" if language == "en" else _tr("رسید تأیید نشد"))

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
            f"{_tr('❌ <b>رسید پرداخت شما تأیید نشد</b>\n\n🆔 شناسه پرداخت: <code>')}{payment['id']}{_tr('</code>\n📝 دلیل: ')}{html.escape(str(reason))}{_tr('\n\nمی\u200cتوانید پس از رفع مشکل، رسید جدیدی ثبت کنید.')}"
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
                _tr("💎 <b>خرید اشتراک</b>\n\nپلن موردنظر را انتخاب کنید:")
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
        return (_tr("👤 <b>وضعیت اشتراک</b>\n\n") if is_fa else "👤 <b>My subscription</b>\n\n") + (
            _tr("در حال حاضر اشتراک فعالی ندارید.") if is_fa else "You do not have an active subscription."
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
        30: (_tr("یک‌ماهه"), "1 month"),
        90: (_tr("سه‌ماهه"), "3 months"),
        180: (_tr("شش‌ماهه"), "6 months"),
        365: (_tr("یک‌ساله"), "1 year"),
    }
    duration = duration_labels.get(duration_days, (f"{duration_days}{_tr(' روز')}", f"{duration_days} days"))[0 if is_fa else 1]
    try:
        expiry = datetime.fromisoformat(str(result["expires_at"]).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=DISPLAY_TIMEZONE)
        remaining_days = max(0, int((expiry - datetime.now(expiry.tzinfo)).total_seconds() + 86399) // 86400)
    except (KeyError, TypeError, ValueError):
        remaining_days = 0
    limit = result.get("daily_download_limit")
    limit_text = _tr("♾️ نامحدود") if is_fa and limit is None else ("♾️ Unlimited" if limit is None else str(limit))
    remaining = _tr("♾️ نامحدود") if is_fa and result.get("remaining_downloads") is None else ("♾️ Unlimited" if result.get("remaining_downloads") is None else str(result.get("remaining_downloads")))
    labels = (_tr("مدت اشتراک"), "Subscription duration", _tr("تاریخ عضویت"), "Registered", _tr("تعداد دانلودهای انجام‌شده"), "Downloads completed", _tr("محدودیت دانلود روزانه"), "Daily download limit", _tr("دانلود باقیمانده"), "Downloads remaining", _tr("روز باقی‌مانده"), "Days remaining")
    return (
        (_tr("👤 <b>وضعیت اشتراک</b>\n\n") if is_fa else "👤 <b>My subscription</b>\n\n")
        + (_tr("✅ اشتراک شما فعال است.\n") if is_fa else "✅ Your subscription is active.\n")
        + f"💎 {(_tr('پلن') if is_fa else 'Plan')}: <b>{plan_name}</b>\n"
        + f"📦 {labels[0 if is_fa else 1]}: <code>{html.escape(duration)}</code>\n"
        + f"📅 {(_tr('اعتبار تا') if is_fa else 'Valid until')}: <code>{_format_datetime(result.get('expires_at'), language)}</code>\n"
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
        if result.get("scheduled"):
            await message.answer(
                "📅 Scheduled plans" if language == "en" else "📅 اشتراک‌های زمان‌بندی‌شده",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text="📅 " + str(len(result["scheduled"])), callback_data="subscription:schedule:1"
                )]]),
            )
    except BackendAPIError:
        await message.answer("Could not load subscription status." if language == "en" else _tr("دریافت وضعیت اشتراک ممکن نشد."))


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
        await _replace_payment_message(
            callback.message,
            (
                _tr("💎 <b>خرید اشتراک</b>\n\nپلن موردنظر را انتخاب کنید:")
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
            "Payment system is not ready." if language == "en" else _tr("سیستم پرداخت آماده نیست."),
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

        keyboard = await _user_home_inline_keyboard(callback.from_user.id)
        if result.get("scheduled"):
            keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
                text="📅 Scheduled plans" if language == "en" else "📅 اشتراک‌های زمان‌بندی‌شده",
                callback_data="subscription:schedule:1",
            )])
        await callback.message.edit_text(
            _subscription_status_text(result, language),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await callback.answer()
    except BackendAPIError:
        await callback.answer(
            "Could not load subscription status." if language == "en" else _tr("دریافت وضعیت اشتراک ممکن نشد."),
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
                "Purchase expired; choose a plan again." if language == "en" else _tr("درخواست خرید منقضی شده است."),
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
            "Payment system is not ready." if language == "en" else _tr("سیستم پرداخت آماده نیست."),
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
            await callback.answer(_tr("این روش پرداخت فقط برای زبان انگلیسی است."), show_alert=True)
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
        await state.set_state(PaymentStates.waiting_for_txid)
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
            else _tr("شبکه انتخابی در دسترس نیست."),
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
                "The selected plan is not available." if language == "en" else _tr("بسته انتخاب‌شده معتبر نیست."),
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
            "Payment system is not ready." if language == "en" else _tr("سیستم پرداخت آماده نیست."),
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
        await _replace_payment_message(
            callback.message,
            "Subscription purchase cancelled." if language == "en" else _tr("خرید اشتراک لغو شد."),
            reply_markup=await _user_home_inline_keyboard(callback.from_user.id),
        )

    await callback.answer("Cancelled." if language == "en" else _tr("لغو شد."))


async def _submit_payment_from_state(
    message: Message,
    state: FSMContext,
    *,
    receipt_message: Message | None,
    telegram_id: int | None = None,
) -> None:
    if telegram_id is None and message.from_user is None:
        return
    customer_telegram_id = telegram_id or int(message.from_user.id)

    language = "fa"
    try:
        language = normalize_language(
            (await get_telegram_user(customer_telegram_id)).get("effective_language")
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
    txid = str(state_data.get("txid") or "").strip() or None

    if not offer_code or not isinstance(offer, dict):
        await state.clear()
        await message.answer(
            "This purchase expired; choose a plan again."
            if language == "en"
            else _tr("درخواست خرید منقضی شده است؛ دوباره پلن را انتخاب کنید.")
        )
        return

    try:
        routes = await notification_routes()
        payment_chat_id = routes.get("chat_id")
        payment_topic_id = (routes.get("topics") or {}).get("payments")

        if receipt_message is not None and receipt_message.photo:
            receipt = receipt_message.photo[-1]
            file_id = receipt.file_id
            unique_id = receipt.file_unique_id
            file_size = receipt.file_size
            file_type = "photo"
            mime_type = "image/jpeg"
            file_name = None
        elif receipt_message is not None:
            document = receipt_message.document

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
                    if language == "en" else _tr("❌ فرمت رسید مجاز نیست. فقط تصویر یا PDF ارسال کنید.")
                )
                return
        else:
            file_id = None
            unique_id = None
            file_size = None
            file_type = None
            mime_type = None
            file_name = None

        max_size_mb = int((receipt_rules or {}).get("max_size_mb", 10))
        if file_size is not None and file_size > max_size_mb * 1024 * 1024:
            await message.answer(
                f"❌ Receipt is larger than {max_size_mb} MB."
                if language == "en" else f"{_tr('❌ حجم رسید بیشتر از ')}{max_size_mb}{_tr(' مگابایت است.')}"
            )
            return

        if message.from_user is not None and message.from_user.id == customer_telegram_id:
            await register_telegram_user(message)
        result = await create_manual_payment(
            telegram_id=customer_telegram_id,
            offer_code=offer_code,
            receipt_file_id=file_id,
            receipt_file_unique_id=unique_id,
            receipt_file_type=file_type,
            receipt_file_size=file_size,
            receipt_mime_type=mime_type,
            receipt_file_name=file_name,
            user_receipt_message_id=(
                receipt_message.message_id
                if receipt_message is not None
                else int(state_data.get("txid_message_id") or message.message_id)
            ),
            payment_card_id=(
                int(payment_card_id) if payment_card_id is not None else None
            ),
            currency=currency,
            usdt_destination_id=(
                int(usdt_destination_id)
                if usdt_destination_id is not None
                else None
            ),
            txid=txid,
        )
        payment_id = int(result["payment"]["id"])
        caption = _build_admin_caption(result, offer)
        send_kwargs = {
            "chat_id": payment_chat_id,
            "parse_mode": "HTML",
            "message_thread_id": payment_topic_id,
            "reply_markup": build_admin_payment_keyboard(
                payment_id,
                explorer_url=_transaction_explorer_url(result["payment"]),
            ),
        }

        try:
            if not routes.get("enabled") or not payment_chat_id or not payment_topic_id:
                raise RuntimeError("Payments Topic is not configured")
            # Text accommodates complete payment details; attachments have no
            # duplicated action buttons and cannot overflow Telegram captions.
            admin_message = await message.bot.send_message(text=caption, **send_kwargs)
            await set_payment_admin_message(
                payment_id=payment_id, admin_chat_id=payment_chat_id,
                admin_message_id=admin_message.message_id,
                admin_message_thread_id=payment_topic_id,
            )
            if file_id:
                await getattr(message.bot, f"send_{file_type}")(
                    **{file_type: file_id}, chat_id=payment_chat_id,
                    message_thread_id=payment_topic_id,
                )
        except Exception as exc:
            # Submission already committed. The finance panel remains the
            # source of truth even when Telegram delivery is unavailable.
            logger.warning("Payment %s Topic delivery failed: %s", payment_id, type(exc).__name__)

        await state.clear()
        await message.answer(
            (
                "✅ <b>Your USDT payment was submitted</b>\n\n"
                f"Payment ID: <code>{payment_id}</code>\n"
                "You will be notified here after administrator review."
                if language == "en" else
                f"{_tr('✅ <b>رسید شما ثبت شد</b>\n\n🆔 شناسه پرداخت: <code>')}{payment_id}{_tr('</code>\nپس از بررسی مدیر، نتیجه همین\u200cجا اطلاع داده می\u200cشود.')}"
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
                "❌ We could not send the payment to the finance team.\n\n"
                "Please try again later."
                if language == "en" else
                f"{_tr('❌ ارسال رسید به بخش مالی انجام نشد.\n\nلطفاً کمی بعد دوباره تلاش کنید.\n<code>')}{html.escape(str(exc)[:300])}</code>"
            ),
            parse_mode="HTML",
        )


@router.message(PaymentStates.waiting_for_receipt, F.photo | F.document)
async def receive_payment_receipt(
    message: Message,
    state: FSMContext,
) -> None:
    await _submit_payment_from_state(message, state, receipt_message=message)


@router.message(PaymentStates.waiting_for_txid, F.text)
async def receive_usdt_txid(message: Message, state: FSMContext) -> None:
    txid = str(message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,255}", txid):
        await message.answer(
            "❌ Invalid TxID. Send the 16-255 character transaction ID exactly as shown by your wallet."
        )
        return
    await state.update_data(txid=txid, txid_message_id=message.message_id)
    await state.set_state(PaymentStates.waiting_for_usdt_screenshot)
    await message.answer(
        "✅ TxID received.\n\nYou may now send a screenshot/PDF, or submit without one.",
        reply_markup=build_usdt_screenshot_keyboard(),
    )


@router.message(
    PaymentStates.waiting_for_usdt_screenshot,
    F.photo | F.document,
)
async def receive_optional_usdt_screenshot(
    message: Message,
    state: FSMContext,
) -> None:
    await _submit_payment_from_state(message, state, receipt_message=message)


@router.callback_query(
    PaymentStates.waiting_for_usdt_screenshot,
    F.data == "payment:usdt-screenshot:skip",
)
async def skip_usdt_screenshot(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _submit_payment_from_state(
        callback.message,
        state,
        receipt_message=None,
        telegram_id=callback.from_user.id,
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
        if language == "en" else _tr("📎 لطفاً فقط تصویر رسید یا فایل PDF را ارسال کنید.")
    )


@router.message(PaymentStates.waiting_for_txid)
async def invalid_usdt_txid(message: Message) -> None:
    await message.answer("🔗 Send the TxID as text before attaching a screenshot.")


@router.message(PaymentStates.waiting_for_usdt_screenshot)
async def invalid_usdt_screenshot(message: Message) -> None:
    await message.answer(
        "📎 Send an image/PDF, or use “Submit without screenshot”."
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
            _tr("بررسی دسترسی مدیر ممکن نشد."),
            show_alert=True,
        )
        return False

    await callback.answer(_tr("شما دسترسی مدیر ندارید."), show_alert=True)
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
async def request_approval_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not await _ensure_admin(callback):
        return
    payment_id = _parse_admin_payment_id(callback)
    if payment_id is None or not isinstance(callback.message, Message):
        await callback.answer(_tr("شناسه پرداخت نامعتبر است."), show_alert=True)
        return
    await state.set_state(AdminPaymentStates.confirming_approval)
    await state.update_data(
        approval_payment_id=payment_id,
        approval_expires_at=datetime.now(timezone.utc).timestamp() + 300,
    )
    await callback.message.edit_reply_markup(
        reply_markup=build_payment_approval_confirmation_keyboard(payment_id)
    )
    await callback.answer(_tr("برای فعال‌سازی، تأیید نهایی را بزنید."))


@router.callback_query(
    AdminPaymentStates.confirming_approval,
    F.data.startswith("payment_admin:approve-confirm:"),
)
async def approve_payment_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not await _ensure_admin(callback):
        return
    payment_id = _parse_admin_payment_id(callback)
    data = await state.get_data()
    if (
        payment_id is None
        or payment_id != int(data.get("approval_payment_id") or 0)
        or datetime.now(timezone.utc).timestamp()
        > float(data.get("approval_expires_at") or 0)
        or not isinstance(callback.message, Message)
    ):
        await state.clear()
        await callback.answer(_tr("تأیید منقضی یا نامعتبر است."), show_alert=True)
        return

    try:
        result = await approve_manual_payment(
            payment_id=payment_id,
            admin_telegram_id=callback.from_user.id,
        )
        subscription = result.get("subscription") or {}
        status_line = (
            f"{_tr('✅ وضعیت: <b>تأیید شد</b>\n👮 مدیر: <code>')}{callback.from_user.id}{_tr('</code>\n📅 اعتبار تا: <code>')}{_format_datetime(subscription.get('expires_at'))}</code>"
        )
        await _edit_receipt_status(callback.message, status_line)
        await state.clear()

        if not result.get("already_reviewed"):
            try:
                await _notify_user_approved(callback.message, result)
            except Exception as exc:
                await callback.message.reply(
                    f"{_tr('⚠️ پرداخت تأیید شد، اما پیام نتیجه به کاربر نرسید: <code>')}{html.escape(str(exc)[:250])}</code>",
                    parse_mode="HTML",
                )

        callback_text = (
            _tr("این پرداخت قبلاً تأیید شده بود.")
            if result.get("already_reviewed")
            else _tr("پرداخت تأیید و اشتراک تمدید شد.")
        )
        await callback.answer(callback_text)
    except BackendAPIError as exc:
        await callback.answer(str(exc.detail)[:180], show_alert=True)


@router.callback_query(
    F.data.startswith("payment_admin:approve-cancel:"),
)
async def cancel_approval_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    payment_id = _parse_admin_payment_id(callback)
    if isinstance(callback.message, Message) and payment_id is not None:
        await callback.message.edit_reply_markup(
            reply_markup=build_admin_payment_keyboard(payment_id)
        )
    await callback.answer(_tr("تأیید پرداخت لغو شد."))


@router.callback_query(F.data.startswith("payment_admin:reject:"))
async def request_rejection_reason(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not await _ensure_admin(callback):
        return

    payment_id = _parse_admin_payment_id(callback)
    if payment_id is None or not isinstance(callback.message, Message):
        await callback.answer(_tr("شناسه پرداخت نامعتبر است."), show_alert=True)
        return

    await state.set_state(AdminPaymentStates.waiting_for_rejection_reason)
    await state.update_data(
        payment_id=payment_id,
        receipt_chat_id=callback.message.chat.id,
        receipt_message_id=callback.message.message_id,
        receipt_content=callback.message.caption or callback.message.text,
        receipt_is_media=callback.message.caption is not None,
    )
    await callback.message.reply(
        (
            f"{_tr('📝 دلیل رد پرداخت <code>#')}{payment_id}{_tr('</code> را ارسال کنید.\nبرای انصراف از <code>/cancel</code> استفاده کنید.')}"
        ),
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True),
    )
    await callback.answer(_tr("دلیل رد را ارسال کنید."))


@router.message(AdminPaymentStates.waiting_for_rejection_reason, F.text)
async def receive_rejection_reason(
    message: Message,
    state: FSMContext,
) -> None:
    if not await _ensure_message_admin(message):
        await state.clear()
        await message.answer(_tr("شما دسترسی بررسی پرداخت‌ها را ندارید."))
        return

    reason = (message.text or "").strip()

    if reason.lower() == "/cancel":
        await state.clear()
        await message.answer(_tr("رد پرداخت لغو شد."))
        return

    if len(reason) < 2:
        await message.answer(_tr("دلیل رد باید حداقل دو نویسه باشد."))
        return

    if len(reason) > 1000:
        await message.answer("Please use at most 1000 characters.")
        return
    state_data = await state.get_data()
    payment_id = int(state_data["payment_id"])
    await state.set_state(AdminPaymentStates.confirming_rejection)
    await state.update_data(rejection_reason=reason)
    await message.answer(
        f"Reject payment #{payment_id}?\n\nReason: {reason}",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Confirm rejection", callback_data=f"payment_admin:reject-confirm:{payment_id}")],
            [InlineKeyboardButton(text="Cancel", callback_data=f"payment_admin:approve-cancel:{payment_id}")],
        ]),
    )


@router.callback_query(AdminPaymentStates.confirming_rejection, F.data.startswith("payment_admin:reject-confirm:"))
async def confirm_payment_rejection(callback: CallbackQuery, state: FSMContext):
    if not isinstance(callback.message, Message) or not await _ensure_admin(callback):
        return
    state_data = await state.get_data()
    payment_id = int(state_data["payment_id"])
    if _parse_admin_payment_id(callback) != payment_id:
        return await callback.answer("This confirmation expired.", show_alert=True)
    reason = str(state_data["rejection_reason"])
    message = callback.message
    admin_id = callback.from_user.id
    await callback.answer()
    try:
        result = await reject_manual_payment(
            payment_id=payment_id,
            admin_telegram_id=admin_id,
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
            original_content=state_data.get("receipt_content"),
            is_media=bool(state_data.get("receipt_is_media")),
            status_line=(
                f"{_tr('❌ وضعیت: <b>رد شد</b>\n👮 مدیر: <code>')}{admin_id}{_tr('</code>\n📝 دلیل: ')}{html.escape(actual_reason)}"
            ),
        )

        if not result.get("already_reviewed"):
            try:
                await _notify_user_rejected(message, result)
            except Exception as exc:
                await message.answer(
                    f"{_tr('⚠️ پرداخت رد شد، اما پیام نتیجه به کاربر نرسید: <code>')}{html.escape(str(exc)[:250])}</code>",
                    parse_mode="HTML",
                )

        await state.clear()
        result_text = (
            f"{_tr('پرداخت #')}{payment_id}{_tr(' قبلاً رد شده بود.')}"
            if result.get("already_reviewed")
            else f"{_tr('❌ پرداخت #')}{payment_id}{_tr(' رد شد.')}"
        )
        await message.answer(result_text)
    except BackendAPIError as exc:
        await message.answer(
            f"❌ <code>{html.escape(str(exc.detail)[:500])}</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("subscription:schedule:"))
async def subscription_schedule(callback: CallbackQuery):
    if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
        return
    try:
        page = max(1, int(callback.data.rsplit(":", 1)[-1]))
        language = normalize_language((await get_telegram_user(callback.from_user.id)).get("effective_language"))
        result = await get_current_subscription(callback.from_user.id)
        items = result.get("scheduled") or []
        title = "📅 Scheduled plans" if language == "en" else "📅 اشتراک‌های زمان‌بندی‌شده"
        lines = [title, ""]
        for item in items[(page - 1) * 5:page * 5]:
            name = item.get("plan_name_en") if language == "en" else item.get("plan_name")
            lines.append(html.escape(str(name)) + "\n" + _format_datetime(item['started_at'], language) + " → " + _format_datetime(item['expires_at'], language))
        if not items: lines.append("No scheduled plans." if language == "en" else "اشتراک زمان‌بندی‌شده‌ای ندارید.")
        rows = []
        nav = []
        if page > 1: nav.append(InlineKeyboardButton(text="◀️", callback_data=f"subscription:schedule:{page - 1}"))
        if page * 5 < len(items): nav.append(InlineKeyboardButton(text="▶️", callback_data=f"subscription:schedule:{page + 1}"))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton(text="↩️", callback_data="payment:status")])
        await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer()
    except (BackendAPIError, ValueError):
        await callback.answer("Could not load scheduled plans.", show_alert=True)
