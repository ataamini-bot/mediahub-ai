"""Runtime Topic routing; failed Telegram delivery never deletes stored work."""
import html
import logging

from app.services.backend import _payment_request

logger = logging.getLogger(__name__)


async def notification_routes() -> dict:
    return await _payment_request("GET", "/internal/notification-routes")


async def send_attachment(bot, item, **kwargs):
    kind = item.get("file_type")
    file_id = item.get("telegram_file_id")
    if kind in {"photo", "document", "video", "voice"} and file_id:
        return await getattr(bot, f"send_{kind}")(**{kind: file_id}, **kwargs)


async def send_support_notification(bot, ticket, *, initial=False) -> bool:
    """Deliver a stored ticket update to the configured support Topic."""
    from app.keyboards.experience import build_support_admin_keyboard
    try:
        routes = await notification_routes()
        chat_id = routes.get("chat_id")
        topic_id = (routes.get("topics") or {}).get("support")
        if not routes.get("enabled") or not chat_id or not topic_id:
            return False
        user = ticket.get("user") or {}
        latest = (ticket.get("messages") or [{}])[-1]
        body = str(latest.get("body") or "[Attachment]")
        text = (
            f"🛟 <b>Support ticket #{ticket['id']}</b>\n"
            f"User: <code>{user.get('telegram_id')}</code>\n"
            f"Subject: {html.escape(str(ticket.get('category')))}\n"
            f"Status: {html.escape(str(ticket.get('status')))}\n"
            f"Plan: {html.escape(str(ticket.get('plan_name') or 'Free'))}\n\n"
            + html.escape(body[:1800])
        )
        kwargs = {"chat_id": chat_id, "message_thread_id": topic_id}
        sent = await bot.send_message(text=text, parse_mode="HTML",
            reply_markup=build_support_admin_keyboard(int(ticket["id"])), **kwargs)
        if initial:
            await _payment_request("PATCH", f"/admin/support/tickets/{ticket['id']}/admin-message",
                payload={"admin_chat_id": chat_id, "admin_message_id": sent.message_id,
                         "admin_message_thread_id": topic_id})
        await send_attachment(bot, latest, **kwargs)
        return True
    except Exception as exc:
        logger.warning("Support Topic delivery failed: %s", type(exc).__name__)
        return False
