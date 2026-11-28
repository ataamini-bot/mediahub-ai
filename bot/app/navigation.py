"""Recognize persistent navigation before accepting text as media-form input."""
from aiogram.filters import BaseFilter
from aiogram.types import Message

from app.i18n import HOME_BUTTON_ACTIONS
from app.runtime_config import action_for_runtime_text, all_runtime_configurations


class RuntimeHomeButtonFilter(BaseFilter):
    async def __call__(self, message: Message) -> dict | bool:
        text = str(message.text or "").strip()
        if not text or text.startswith("/") or "https://" in text or "http://" in text:
            return False
        legacy = HOME_BUTTON_ACTIONS.get(text)
        if legacy is not None:
            return {"runtime_home_action": {"action": legacy}}
        action = action_for_runtime_text(text, await all_runtime_configurations())
        return {"runtime_home_action": action} if action is not None else False


class MediaFormInputFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        # Let later command/home routers receive these messages. The destination
        # handler owns state cleanup; uploaded or queued work must not be erased here.
        if str(message.text or "").strip().startswith("/"):
            return False
        return not await RuntimeHomeButtonFilter()(message)
