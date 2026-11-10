"""Independent sender: bounded rate, durable claims and no replay on uncertain delivery."""
import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from app.services.backend import BackendAPIError, _payment_request
from app.middleware.interface import ui_passthrough

logger = logging.getLogger(__name__)


async def deliver(bot, chat_id: int, content: dict, button: dict | None = None):
    # Previews and deliveries must not be paginated or rewritten as admin interface text.
    token = ui_passthrough.set(True)
    try:
        return await _deliver(bot, chat_id, content, button)
    finally:
        ui_passthrough.reset(token)


async def _deliver(bot, chat_id: int, content: dict, button: dict | None):
    if chat_id <= 0:
        raise ValueError("Broadcast recipients must be private users")
    kind = content["kind"]
    kwargs = {"chat_id": chat_id, "request_timeout": 45}
    if kind == "forward":
        return await bot.forward_message(from_chat_id=content["source_chat_id"],
            message_id=content["source_message_id"], **kwargs)
    if button:
        kwargs["reply_markup"] = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=button["text"], url=button["url"]),
        ]])
    entities = [MessageEntity.model_validate(item) for item in content.get("entities", [])]
    kwargs["parse_mode"] = None
    if kind == "text":
        return await bot.send_message(text=content["text"], entities=entities, **kwargs)
    return await getattr(bot, f"send_{kind}")(
        **{kind: content["file_id"]}, caption=content.get("text") or None,
        caption_entities=entities, **kwargs,
    )


async def send_claim(bot, claim):
    try:
        result = await deliver(bot, claim["telegram_id"], claim["content"], claim.get("button"))
        return {"outcome": "sent", "message_id": result.message_id}
    except TelegramRetryAfter as exc:
        return {"outcome": "retry", "retry_after": min(86400, max(1, int(exc.retry_after))), "error_code": "flood_wait"}
    except TelegramForbiddenError:
        return {"outcome": "blocked", "error_code": "forbidden"}
    except TelegramBadRequest as exc:
        unavailable = any(value in str(exc).lower() for value in ("chat not found", "user is deactivated"))
        return {"outcome": "failed", "error_code": "chat_unavailable" if unavailable else "invalid_content"}
    except Exception as exc:
        # A timeout/disconnect may occur after Telegram accepted the message.
        logger.warning("Broadcast %s delivery uncertain: %s", claim["broadcast_id"], type(exc).__name__)
        return {"outcome": "uncertain", "error_code": "transport_uncertain"}


async def run_broadcast_sender(bot):
    pending_ack = None
    while True:
        try:
            if pending_ack is not None:
                recipient_id, payload = pending_ack
                try:
                    await _payment_request("POST", f"/internal/broadcasts/{recipient_id}/ack", payload=payload)
                except BackendAPIError as exc:
                    if exc.status_code not in {404, 409}:
                        raise
                    # The durable claim no longer exists/matches, e.g. after recovery.
                    # Never send again just because this acknowledgement cannot be stored.
                    logger.warning("Broadcast acknowledgement no longer matches recipient %s", recipient_id)
                pending_ack = None
                await asyncio.sleep(0.15)
                continue
            claim = await _payment_request("POST", "/internal/broadcasts/claim")
            if not claim:
                await asyncio.sleep(1)
                continue
            result = await send_claim(bot, claim)
            pending_ack = (claim["recipient_id"], {**result, "claim_token": claim["claim_token"]})
        except asyncio.CancelledError:
            # Leases left by shutdown become uncertain, never automatically resent.
            raise
        except Exception as exc:
            # Retry acknowledgement rather than delivering the message again.
            logger.warning("Broadcast sender backend unavailable: %s", type(exc).__name__)
            await asyncio.sleep(5)
