import html
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, Message

from app.keyboards.payment import (
    build_admin_payment_keyboard,
    build_home_keyboard,
    build_payment_offers_keyboard,
    build_receipt_cancel_keyboard,
    format_toman,
)
from app.services.backend import (
    BackendAPIError,
    approve_manual_payment,
    create_manual_payment,
    get_current_subscription,
    get_payment_configuration,
    mark_payment_delivery_failed,
    register_telegram_user,
    reject_manual_payment,
    set_payment_admin_message,
)
from app.state.payment import AdminPaymentStates, PaymentStates


router = Router(name="payments")


def _parse_int_env(name: str) -> int | None:
    raw_value = os.getenv(name, "").strip()

    if not raw_value:
        return None

    try:
        return int(raw_value)
    except ValueError:
        return None


def _admin_id_set() -> frozenset[int]:
    raw_value = os.getenv("TELEGRAM_ADMIN_IDS", "")
    values = re.split(r"[\s,;]+", raw_value.strip())
    result = set()

    for value in values:
        if not value.strip():
            continue

        try:
            result.add(int(value.strip()))
        except ValueError:
            continue

    return frozenset(result)


ADMIN_PAYMENT_CHAT_ID = _parse_int_env("ADMIN_NOTIFICATIONS_CHAT_ID")
ADMIN_PAYMENT_TOPIC_ID = _parse_int_env(
    "ADMIN_NOTIFICATIONS_PAYMENTS_TOPIC_ID"
)
DISPLAY_TIMEZONE = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Tehran"))


def _find_offer(configuration: dict, code: str) -> dict | None:
    return next(
        (
            offer
            for offer in configuration.get("offers", [])
            if offer.get("code") == code
        ),
        None,
    )


def _format_datetime(value: str | None) -> str:
    if not value:
        return "نامشخص"

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)


def _payment_error_message(exc: BackendAPIError) -> str:
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

    if exc.status_code == 503:
        return (
            "⚙️ سیستم پرداخت هنوز به‌طور کامل تنظیم نشده است.\n\n"
            "لطفاً با پشتیبانی تماس بگیرید."
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

    return (
        "🧾 <b>رسید جدید خرید اشتراک</b>\n\n"
        f"🆔 شناسه پرداخت: <code>{payment['id']}</code>\n"
        f"👤 نام: {html.escape(full_name)}\n"
        f"🔗 نام کاربری: {html.escape(username_text)}\n"
        f"📱 Telegram ID: <code>{user['telegram_id']}</code>\n\n"
        f"💎 بسته: <b>{html.escape(offer['label'])}</b>\n"
        f"💰 مبلغ: <b>{format_toman(payment['amount'])}</b>\n"
        f"📅 مدت: <code>{payment['duration_months']} ماه</code>\n"
        f"📎 نوع رسید: <code>{payment['receipt_file_type']}</code>\n\n"
        "⏳ وضعیت: <b>در انتظار بررسی</b>"
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

    await message.bot.send_message(
        chat_id=user["telegram_id"],
        text=(
            "✅ <b>پرداخت شما تأیید شد</b>\n\n"
            f"🆔 شناسه پرداخت: <code>{payment['id']}</code>\n"
            f"💎 مدت افزوده‌شده: <code>{payment['duration_months']} ماه</code>\n"
            "📅 اعتبار اشتراک تا: "
            f"<code>{_format_datetime(subscription.get('expires_at'))}</code>"
        ),
        parse_mode="HTML",
    )


async def _notify_user_rejected(message: Message, result: dict) -> None:
    user = result["user"]
    payment = result["payment"]
    reason = payment.get("rejection_reason") or "رسید تأیید نشد"

    await message.bot.send_message(
        chat_id=user["telegram_id"],
        text=(
            "❌ <b>رسید پرداخت شما تأیید نشد</b>\n\n"
            f"🆔 شناسه پرداخت: <code>{payment['id']}</code>\n"
            f"📝 دلیل: {html.escape(reason)}\n\n"
            "می‌توانید پس از رفع مشکل، رسید جدیدی ثبت کنید."
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "payment:open")
async def open_payment_offers(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        return

    try:
        configuration = await get_payment_configuration()
        await state.clear()
        await callback.message.edit_text(
            (
                "💎 <b>خرید اشتراک Premium</b>\n\n"
                "مدت موردنظر را انتخاب کنید:"
            ),
            parse_mode="HTML",
            reply_markup=build_payment_offers_keyboard(
                configuration["offers"]
            ),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(
            "سیستم پرداخت آماده نیست.",
            show_alert=True,
        )
        await callback.message.answer(
            _payment_error_message(exc),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "payment:status")
async def payment_status(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return

    try:
        result = await get_current_subscription(callback.from_user.id)

        if not result.get("is_active"):
            text = (
                "👤 <b>وضعیت اشتراک</b>\n\n"
                "در حال حاضر اشتراک Premium فعال ندارید."
            )
        else:
            text = (
                "👤 <b>وضعیت اشتراک</b>\n\n"
                "✅ اشتراک Premium شما فعال است.\n"
                "📅 اعتبار تا: "
                f"<code>{_format_datetime(result.get('expires_at'))}</code>"
            )

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=build_home_keyboard(),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(
            "دریافت وضعیت اشتراک ممکن نشد.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("payment:offer:"))
async def select_payment_offer(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    offer_code = callback.data.rsplit(":", 1)[-1]

    try:
        configuration = await get_payment_configuration()
        offer = _find_offer(configuration, offer_code)

        if offer is None:
            await callback.answer("بسته انتخاب‌شده معتبر نیست.", show_alert=True)
            return

        destination = configuration["destination"]
        receipt_rules = configuration["receipt"]
        await state.set_state(PaymentStates.waiting_for_receipt)
        await state.update_data(offer_code=offer_code)

        bank_line = ""
        if destination.get("bank_name"):
            bank_line = (
                "\n🏦 بانک: "
                f"<b>{html.escape(destination['bank_name'])}</b>"
            )

        await callback.message.edit_text(
            (
                f"💎 <b>{html.escape(offer['label'])}</b>\n"
                f"💰 مبلغ: <b>{format_toman(offer['price'])}</b>\n\n"
                "لطفاً مبلغ را به کارت زیر واریز کنید:\n\n"
                f"💳 <code>{html.escape(destination['card_number'])}</code>\n"
                "👤 به نام: "
                f"<b>{html.escape(destination['card_holder'])}</b>"
                f"{bank_line}\n\n"
                "📎 سپس تصویر رسید یا فایل PDF را همین‌جا ارسال کنید.\n"
                "حداکثر حجم رسید: "
                f"<code>{receipt_rules['max_size_mb']} MB</code>"
            ),
            parse_mode="HTML",
            reply_markup=build_receipt_cancel_keyboard(),
        )
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer("سیستم پرداخت آماده نیست.", show_alert=True)
        await callback.message.answer(
            _payment_error_message(exc),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "payment:cancel")
async def cancel_payment_flow(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "خرید اشتراک لغو شد.",
            reply_markup=build_home_keyboard(),
        )

    await callback.answer("لغو شد.")


@router.message(PaymentStates.waiting_for_receipt, F.photo | F.document)
async def receive_payment_receipt(
    message: Message,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return

    state_data = await state.get_data()
    offer_code = state_data.get("offer_code")

    if not offer_code:
        await state.clear()
        await message.answer("درخواست خرید منقضی شده است؛ دوباره پلن را انتخاب کنید.")
        return

    try:
        configuration = await get_payment_configuration()
        offer = _find_offer(configuration, offer_code)

        if offer is None:
            raise RuntimeError("Selected payment offer is no longer available")

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

            allowed_types = set(configuration["receipt"]["allowed_types"])
            if mime_type not in allowed_types:
                await message.answer(
                    "❌ فرمت رسید مجاز نیست. فقط تصویر یا PDF ارسال کنید."
                )
                return

        max_size_mb = int(configuration["receipt"]["max_size_mb"])
        if file_size is not None and file_size > max_size_mb * 1024 * 1024:
            await message.answer(
                f"❌ حجم رسید بیشتر از {max_size_mb} مگابایت است."
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
                "✅ <b>رسید شما ثبت شد</b>\n\n"
                f"🆔 شناسه پرداخت: <code>{payment_id}</code>\n"
                "پس از بررسی مدیر، نتیجه همین‌جا اطلاع داده می‌شود."
            ),
            parse_mode="HTML",
            reply_markup=build_home_keyboard(),
        )
    except BackendAPIError as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "pending_payment_exists":
            await state.clear()

        await message.answer(
            _payment_error_message(exc),
            parse_mode="HTML",
        )
    except Exception as exc:
        await message.answer(
            (
                "❌ ارسال رسید به بخش مالی انجام نشد.\n\n"
                "لطفاً کمی بعد دوباره تلاش کنید.\n"
                f"<code>{html.escape(str(exc)[:300])}</code>"
            ),
            parse_mode="HTML",
        )


@router.message(PaymentStates.waiting_for_receipt)
async def invalid_payment_receipt(message: Message) -> None:
    await message.answer(
        "📎 لطفاً فقط تصویر رسید یا فایل PDF را ارسال کنید."
    )


def _parse_admin_payment_id(callback: CallbackQuery) -> int | None:
    if not callback.data:
        return None

    try:
        return int(callback.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None


async def _ensure_admin(callback: CallbackQuery) -> bool:
    if callback.from_user.id in _admin_id_set():
        return True

    await callback.answer("شما دسترسی مدیر ندارید.", show_alert=True)
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
    if message.from_user is None or message.from_user.id not in _admin_id_set():
        await state.clear()
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
