"""Private support alerts and Topic reports for already stored work."""
import logging

from aiogram.types import LinkPreviewOptions, ReplyParameters

from app.middleware.interface import ui_language
from app.runtime_config import runtime_configuration
from app.services.backend import _payment_request
from app.tool_texts import tool_text

logger = logging.getLogger(__name__)


async def notification_routes() -> dict:
    return await _payment_request("GET", "/internal/notification-routes")


async def send_attachment(bot, item, **kwargs):
    kind = item.get("file_type")
    file_id = item.get("telegram_file_id")
    if kind in {"photo", "document", "video", "voice"} and file_id:
        return await getattr(bot, f"send_{kind}")(**{kind: file_id}, **kwargs)


def _bounded(value, limit):
    # Telegram counts UTF-16 code units. Plain text can be safely shortened even
    # when a ticket contains markup, emoji or an administrator repeats a field.
    value = str(value)
    encoded = value.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return value
    return encoded[:(limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def _support_card(ticket, *, initial):
    from app.keyboards.experience import SUPPORT_CATEGORY_LABELS
    user = ticket.get("user") or {}
    latest = (ticket.get("messages") or [{}])[-1]
    language = "en" if ui_language.get() == "en" else "fa"
    name = " ".join(str(user.get(key) or "") for key in ("first_name", "last_name")).strip()
    category = SUPPORT_CATEGORY_LABELS[language].get(ticket.get("category"), ticket.get("category") or "—")
    return _bounded(tool_text(
        "support_private_new" if initial else "support_private_reply",
        ticket_id=_bounded(ticket["id"], 20),
        name=_bounded(name or "—", 120),
        telegram_id=_bounded(user.get("telegram_id") or "—", 32),
        username=_bounded("@" + user["username"] if user.get("username") else "—", 66),
        category=_bounded(category, 60),
        plan=_bounded(ticket.get("plan_name") or ("Free" if language == "en" else "رایگان"), 100),
        body=_bounded(latest.get("body") or tool_text("support_attachment_only"), 2000),
    ), 3900)


async def _send_support_card(bot, ticket, *, initial, language, chat_id, topic_id=None):
    from app.keyboards.experience import build_support_admin_keyboard
    token = ui_language.set(language)
    try:
        await runtime_configuration(language)
        kwargs = {"chat_id": chat_id}
        if topic_id is not None:
            kwargs["message_thread_id"] = topic_id
        sent = await bot.send_message(text=_support_card(ticket, initial=initial), parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=build_support_admin_keyboard(int(ticket["id"])), **kwargs)
        # A file failure must not discard an already delivered alert. Stored
        # attachments remain accessible through the ticket's Details button.
        try:
            await send_attachment(bot, (ticket.get("messages") or [{}])[-1],
                reply_parameters=ReplyParameters(message_id=sent.message_id, allow_sending_without_reply=True),
                **kwargs)
        except Exception as exc:
            logger.warning("Support attachment delivery failed: %s", type(exc).__name__)
        return sent
    finally:
        ui_language.reset(token)


async def send_support_notification(bot, ticket, *, initial=False) -> bool:
    """Notify eligible administrators privately, independently of Topic routing.

    Recipients come from the authenticated backend's current support permissions.
    One blocked administrator or unavailable Topic never prevents other alerts.
    """
    delivered = False
    languages = ticket.get("recipient_languages") or {}
    recipients = sorted({value for value in ticket.get("recipients", [])
                         if type(value) is int and value > 0})
    for chat_id in recipients:
        language = languages.get(str(chat_id), languages.get(chat_id, "fa"))
        language = "en" if language == "en" else "fa"
        try:
            await _send_support_card(bot, ticket, initial=initial, language=language, chat_id=chat_id)
            delivered = True
        except Exception as exc:
            logger.warning("Private support delivery failed: %s", type(exc).__name__)

    try:
        routes = await notification_routes()
        chat_id = routes.get("chat_id")
        topic_id = (routes.get("topics") or {}).get("support")
        if routes.get("enabled") and chat_id and topic_id:
            sent = await _send_support_card(bot, ticket, initial=initial, language="fa",
                                            chat_id=chat_id, topic_id=topic_id)
            delivered = True
            if initial:
                await _payment_request("PATCH", f"/admin/support/tickets/{ticket['id']}/admin-message",
                    payload={"admin_chat_id": chat_id, "admin_message_id": sent.message_id,
                             "admin_message_thread_id": topic_id})
    except Exception as exc:
        logger.warning("Support Topic delivery failed: %s", type(exc).__name__)
    return delivered
