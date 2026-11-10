"""Receipts stay in private admin chats; Topics receive approval reports only."""
import logging

from app.localization import tr
from app.services.topic_delivery import notification_routes

logger = logging.getLogger(__name__)


async def send_private_receipt(bot, *, chat_id, payment, text, reply_markup):
    if int(chat_id) <= 0:
        raise ValueError("Payment receipts require a private chat")
    kind = payment.get("receipt_file_type")
    file_id = payment.get("receipt_file_id")
    if file_id and kind in {"photo", "document"}:
        try:
            await getattr(bot, f"send_{kind}")(
                chat_id=chat_id, **{kind: file_id},
                caption=f"{tr('🧾 رسید پرداخت')} #{int(payment['id'])}",
            )
        except Exception as exc:
            logger.warning("Payment %s attachment failed: %s", payment['id'], type(exc).__name__)
            text += "\n\n" + tr("⚠️ پیوست نمایش داده نشد؛ برای تلاش مجدد، رسید را از فهرست پرداخت‌ها باز کنید.")
    return await bot.send_message(
        chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup,
    )


async def send_approval_report(bot, text: str) -> bool | None:
    """None means deliberately disabled; False means delivery needs a retry."""
    try:
        routes = await notification_routes()
        if not routes.get("enabled"):
            return None
        chat_id = routes.get("chat_id")
        topic_id = (routes.get("topics") or {}).get("payments")
        if not chat_id or not topic_id:
            return False
        await bot.send_message(
            chat_id=chat_id, message_thread_id=topic_id, text=text, parse_mode="HTML",
        )
        return True
    except Exception as exc:
        logger.warning("Payment approval report failed: %s", type(exc).__name__)
        return False
