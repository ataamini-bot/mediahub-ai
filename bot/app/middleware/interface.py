from app.localization import tr as _tr, localized_collection as _localized_collection
"""Bound, expiring confirmations and pagination shared by all inline menus."""
import hashlib
import json
import re
import secrets
from contextvars import ContextVar

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

ui_actor = ContextVar("ui_actor", default=None)
ui_state = ContextVar("ui_state", default=None)
ui_language = ContextVar("ui_language", default="fa")

# These callbacks already follow a review screen. Wrap their final button.
FINAL = re.compile(
    r"^(?:admin:(?:change:(?:confirm|final)|finance:confirm|setting:confirm|"
    r"plan:[a-z_]+:confirm|homebutton:delete:[0-9]+|channel:delete:[0-9]+)|"
    r"payment_admin:(?:approve-confirm|reject-confirm):[0-9]+|ops:confirm)$"
)
# These legacy actions previously wrote immediately from a detail screen.
DIRECT = re.compile(
    r"^(?:admin:(?:homebutton|channel):toggle:[0-9]+|"
    r"support_admin:(?:close|reopen):[0-9]+|"
    r"support_admin:status:closed:[0-9]+)$"
)


async def fingerprint(state):
    if state is None:
        return None
    value = [await state.get_state(), await state.get_data()]
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def page_markup(rows, token, page):
    # Keep the last row (Back/Cancel/Refresh) accessible on every page.
    body, footer = rows[:-1], rows[-1:]
    pages = max(1, (len(body) + 6) // 7)
    page = min(max(1, page), pages)
    nav = []
    if page > 1:
        nav.append({"text": "◀️", "callback_data": f"page:{token}:{page - 1}"})
    nav.append({"text": f"{page}/{pages}", "callback_data": f"page:{token}:{page}"})
    if page < pages:
        nav.append({"text": "▶️", "callback_data": f"page:{token}:{page + 1}"})
    return InlineKeyboardMarkup(inline_keyboard=body[(page - 1) * 7:page * 7] + [nav] + footer)


class InterfaceContext(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from app.services.backend import get_telegram_user
        from app.i18n import normalize_language
        user = getattr(event, "from_user", None)
        language = normalize_language(getattr(user, "language_code", None))
        if user:
            try:
                language = normalize_language((await get_telegram_user(user.id)).get("effective_language"))
            except Exception:
                pass
        tokens = (ui_actor.set(user.id if user else None), ui_state.set(data.get("state")), ui_language.set(language))
        try:
            return await handler(event, data)
        finally:
            ui_actor.reset(tokens[0]); ui_state.reset(tokens[1]); ui_language.reset(tokens[2])


class InterfaceRequests(BaseRequestMiddleware):
    def __init__(self, redis):
        self.redis = redis

    async def __call__(self, make_request, bot, method):
        markup = getattr(method, "reply_markup", None)
        body = getattr(method, "text", None)
        long_text = isinstance(body, str) and len(body.encode('utf-16-le')) > 7800
        if long_text and markup is None:
            markup = InlineKeyboardMarkup(inline_keyboard=[])
        if not isinstance(markup, InlineKeyboardMarkup):
            return await make_request(bot, method)
        rows = [[b.model_dump(exclude_none=True) for b in row] for row in markup.inline_keyboard]
        pending = []
        actor = ui_actor.get()
        state_hash = await fingerprint(ui_state.get())
        chat_id = getattr(method, "chat_id", None)
        for row in rows:
            for button in row:
                value = button.get("callback_data") or ""
                mode = "commit" if FINAL.fullmatch(value) else "ask" if DIRECT.fullmatch(value) else None
                if mode and actor:
                    token = secrets.token_urlsafe(12)
                    payload = {"actor": actor, "chat": chat_id, "action": value,
                               "state": state_hash, "mode": mode, "label": button["text"]}
                    button["callback_data"] = f"gate:{token}"
                    pending.append((f"mediahub:ui:gate:{token}", payload, 300))
        has_pages = any((b.get("callback_data") or "").startswith(("page:", "textpage:")) for row in rows for b in row)
        row_token = None
        if len(rows) > 9 and not has_pages and actor:
            token = secrets.token_urlsafe(12)
            row_token = token
            payload = {"actor": actor, "chat": chat_id, "rows": rows, "current": 1}
            pending.append((f"mediahub:ui:page:{token}", payload, 1800))
            markup = page_markup(rows, token, 1)
        else:
            markup = InlineKeyboardMarkup(inline_keyboard=rows)
        if long_text and actor and not any((b.get("callback_data") or "").startswith("textpage:") for row in rows for b in row):
            from app.utils.text_pages import text_pages
            pages = text_pages(body, html=str(getattr(method, 'parse_mode', '')).lower() == 'html')
            if len(pages) > 1:
                token = secrets.token_urlsafe(12)
                rendered_rows = [[b.model_dump(exclude_none=True) for b in r] for r in markup.inline_keyboard]
                payload = {"actor": int(chat_id) if str(chat_id).isdigit() else actor, "chat": chat_id,
                           "parts": pages, "rows": rendered_rows, "current": 1, "row_token": row_token}
                pending.append((f"mediahub:ui:textpage:{token}", payload, 1800))
                if row_token:
                    for key, row_payload, _ttl in pending:
                        if key == f"mediahub:ui:page:{row_token}":
                            row_payload["text_token"] = token
                markup = text_page_markup(rendered_rows, token, 1, len(pages))
                method = method.model_copy(update={"text": pages[0], "parse_mode": None, "entities": None})
        # Store before send; bind the Telegram message immediately afterwards.
        for key, payload, ttl in pending:
            await self.redis.set(key, json.dumps(payload), ex=ttl)
        result = await make_request(bot, method.model_copy(update={"reply_markup": markup}))
        message_id = getattr(result, "message_id", None) or getattr(method, "message_id", None)
        for key, payload, ttl in pending:
            payload["message"] = message_id
            await self.redis.set(key, json.dumps(payload), ex=ttl)
        return result


class InterfaceCallbacks(BaseMiddleware):
    def __init__(self, redis):
        self.redis = redis

    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery) or not isinstance(event.message, Message):
            return await handler(event, data)
        value = event.data or ""
        if value == "gate_cancel":
            await event.message.edit_reply_markup(reply_markup=None)
            return await event.answer("Cancelled." if ui_language.get() == "en" else _tr("لغو شد."))
        if not value.startswith(("gate:", "page:", "textpage:")):
            if FINAL.fullmatch(value) or DIRECT.fullmatch(value):
                return await event.answer("This button expired. Open the menu again.", show_alert=True)
            return await handler(event, data)
        parts = value.split(":")
        key = f"mediahub:ui:{parts[0]}:{parts[1]}"
        raw = await self.redis.get(key)
        payload = json.loads(raw) if raw else None
        valid = payload and payload.get("actor") == event.from_user.id and str(payload.get("chat")) == str(event.message.chat.id) and payload.get("message") == event.message.message_id
        if not valid:
            return await event.answer("This menu expired or belongs to another administrator. Open it again.", show_alert=True)
        if parts[0] == "textpage":
            try:
                page = min(max(1, int(parts[2])), len(payload["parts"]))
            except (ValueError, IndexError):
                return await event.answer()
            if page == payload.get("current", 1):
                return await event.answer()
            rows = payload["rows"]
            if payload.get("row_token"):
                row_raw = await self.redis.get(f"mediahub:ui:page:{payload['row_token']}")
                if row_raw:
                    row_payload = json.loads(row_raw)
                    rows = page_markup(row_payload["rows"], payload["row_token"], row_payload.get("current", 1)).inline_keyboard
            try:
                await event.message.edit_text(payload["parts"][page - 1], parse_mode=None,
                    reply_markup=text_page_markup(rows, parts[1], page, len(payload["parts"])))
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
            payload["current"] = page
            await self.redis.set(key, json.dumps(payload), ex=1800)
            return await event.answer()
        if parts[0] == "page":
            try:
                page = int(parts[2])
            except (ValueError, IndexError):
                return await event.answer()
            page = min(max(1, page), max(1, (len(payload["rows"]) - 1 + 6) // 7))
            if page == payload.get("current", 1):
                return await event.answer()
            markup = page_markup(payload["rows"], parts[1], page)
            if payload.get("text_token"):
                text_raw = await self.redis.get(f"mediahub:ui:textpage:{payload['text_token']}")
                if text_raw:
                    text_payload = json.loads(text_raw)
                    markup = text_page_markup(markup.inline_keyboard, payload["text_token"], text_payload.get("current", 1), len(text_payload["parts"]))
            try:
                await event.message.edit_reply_markup(reply_markup=markup)
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
            payload["current"] = page
            await self.redis.set(key, json.dumps(payload), ex=1800)
            return await event.answer()
        if payload["state"] != await fingerprint(data.get("state")):
            return await event.answer("The form changed. Review the current form again.", show_alert=True)
        # Consume atomically. Concurrent clicks can never both dispatch.
        if not await self.redis.getdel(key):
            return await event.answer("Already handled.", show_alert=True)
        if payload["mode"] == "ask":
            token = secrets.token_urlsafe(12)
            payload = {**payload, "mode": "commit"}
            is_en = ui_language.get() == "en"
            text = ("Confirm this action?" if is_en else _tr("این عملیات تأیید شود؟")) + "\n\n" + payload["label"] + "\n" + (event.message.text or event.message.caption or "")[:2300]
            sent = await event.message.answer(text, parse_mode=None, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Confirm" if is_en else _tr("✅ تأیید"), callback_data=f"gate:{token}")],
                [InlineKeyboardButton(text="Cancel" if is_en else _tr("انصراف"), callback_data="gate_cancel")],
            ]))
            payload["message"] = sent.message_id
            await self.redis.set(f"mediahub:ui:gate:{token}", json.dumps(payload), ex=300)
            return await event.answer()
        # Endpoint/handler authorization is still checked on every commit.
        return await handler(event.model_copy(update={"data": payload["action"]}), data)


def text_page_markup(rows, token, page, total):
    navigation = []
    if page > 1:
        navigation.append({"text": "◀️", "callback_data": f"textpage:{token}:{page - 1}"})
    navigation.append({"text": f"📄 {page}/{total}", "callback_data": f"textpage:{token}:{page}"})
    if page < total:
        navigation.append({"text": "▶️", "callback_data": f"textpage:{token}:{page + 1}"})
    return InlineKeyboardMarkup(inline_keyboard=[navigation] + rows)
