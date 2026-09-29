"""Deliver committed balance notifications separately from money transactions."""
import asyncio
import logging
from decimal import Decimal

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from app.services.backend import BackendAPIError, _payment_request
from app.services.topic_delivery import notification_routes

logger = logging.getLogger(__name__)


def amount(value, currency):
    number = Decimal(str(value))
    return f"{number:,.0f} تومان" if currency == "IRT" else f"{number:,.4f} USDT"


def user_text(notice):
    en = notice["language"] == "en"
    if notice["currency"] != ("USDT" if en else "IRT"):
        return ("Your internal credit was updated. The balance for each language is available on its credit page."
                if en else "اعتبار داخلی شما به‌روزرسانی شد. موجودی مربوط به هر زبان در صفحهٔ اعتبار داخلی آن زبان دیده می‌شود.")
    kind = notice["entry_kind"]
    title = ("Subscription paid with internal credit" if en else "خرید اشتراک با اعتبار داخلی ثبت شد") if kind == "purchase" else (
        "Your internal credit was updated" if en else "اعتبار داخلی شما به‌روزرسانی شد")
    sign = "+" if Decimal(notice["delta"]) > 0 else ""
    return (f"✅ {title}\n{sign}{amount(notice['delta'], notice['currency'])}\n"
            f"{'Balance after this transaction' if en else 'موجودی پس از این تراکنش'}: {amount(notice['balance_after'], notice['currency'])}")


def report_text(notice):
    return (f"✅ خرید تأییدشده با اعتبار داخلی\nپرداخت: {notice['payment_id']}\n"
            f"Telegram ID: {notice['telegram_id']}\nپلن: {notice['plan_name']}\n"
            f"مدت: {notice['duration_days']} روز\nمبلغ: {amount(-Decimal(notice['delta']), notice['currency'])}\n"
            "روش: اعتبار داخلی؛ واریز خارجی جدید نیست.")


async def send_notice(bot, notice):
    try:
        if notice["kind"] == "report":
            routes = await notification_routes()
            if not routes.get("enabled"):
                return {"status": "disabled"}
            chat_id, topic = routes.get("chat_id"), (routes.get("topics") or {}).get("payments")
            if not chat_id or not topic:
                return {"status": "failed"}
            await bot.send_message(chat_id=chat_id, message_thread_id=topic, text=report_text(notice),
                                   parse_mode=None, request_timeout=45)
        else:
            await bot.send_message(chat_id=notice["telegram_id"], text=user_text(notice), parse_mode=None, request_timeout=45)
        return {"status": "sent"}
    except TelegramRetryAfter as exc:
        return {"status": "retry", "retry_after": min(86400, max(1, int(exc.retry_after)))}
    except (TelegramForbiddenError, TelegramBadRequest):
        return {"status": "failed"}
    except Exception as exc:
        logger.warning("Credit notification %s uncertain: %s", notice["id"], type(exc).__name__)
        return {"status": "uncertain"}


async def run_credit_notices(bot):
    pending_ack = None
    while True:
        try:
            if pending_ack is not None:
                notice_id, payload = pending_ack
                try:
                    await _payment_request("POST", f"/internal/credit/notices/{notice_id}/ack", payload=payload)
                except BackendAPIError as exc:
                    if exc.status_code not in {404, 409}:
                        raise
                pending_ack = None
                await asyncio.sleep(0.2)
                continue
            notice = await _payment_request("POST", "/internal/credit/notices/claim")
            if notice is None:
                await asyncio.sleep(2)
                continue
            outcome = await send_notice(bot, notice)
            pending_ack = notice["id"], {**outcome, "claim_token": notice["claim_token"]}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Credit notice worker unavailable: %s", type(exc).__name__)
            await asyncio.sleep(5)
