from datetime import datetime

from pydantic import BaseModel


class LanguageUpdate(BaseModel):
    language: str


class TelegramUserResponse(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    preferred_language: str | None
    effective_language: str
    status: str
    is_admin: bool
    last_activity_at: datetime | None
