from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ButtonAction = Literal[
    "url",
    "message",
    "buy",
    "subscription",
    "support",
    "tutorial",
    "faq",
]
ButtonStyle = Literal["default", "primary", "success", "danger"]
SupportCategory = Literal["financial", "technical", "account", "general"]
SupportFileType = Literal["photo", "document", "video", "voice"]


class BotConfigurationResponse(BaseModel):
    language: Literal["fa", "en"]
    content: dict[str, str]
    buttons: dict[str, str]
    custom_buttons: list[dict[str, Any]]
    required_channels: list[dict[str, Any]]


class HomeButtonCreate(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    label_fa: str = Field(min_length=1, max_length=64)
    label_en: str = Field(min_length=1, max_length=64)
    action_type: ButtonAction
    action_value: str | None = Field(default=None, max_length=3900)
    style: ButtonStyle = "default"
    sort_order: int = Field(default=100, ge=0, le=100_000)
    is_active: bool = True


class HomeButtonUpdate(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    label_fa: str | None = Field(default=None, min_length=1, max_length=64)
    label_en: str | None = Field(default=None, min_length=1, max_length=64)
    action_type: ButtonAction | None = None
    action_value: str | None = Field(default=None, max_length=3900)
    style: ButtonStyle | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None

    @model_validator(mode="after")
    def at_least_one_change(self):
        if self.model_fields_set == {"actor_telegram_id"}:
            raise ValueError("At least one home button field must change")
        return self


class HomeButtonResponse(BaseModel):
    id: int
    label_fa: str
    label_en: str
    action_type: ButtonAction
    action_value: str | None
    style: ButtonStyle
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RequiredChannelCreate(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    chat_id: str = Field(min_length=6, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    invite_url: str = Field(min_length=10, max_length=500)
    sort_order: int = Field(default=0, ge=0, le=100_000)
    is_active: bool = True


class RequiredChannelUpdate(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    chat_id: str | None = Field(default=None, min_length=6, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    invite_url: str | None = Field(default=None, min_length=10, max_length=500)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None

    @model_validator(mode="after")
    def at_least_one_change(self):
        if self.model_fields_set == {"actor_telegram_id"}:
            raise ValueError("At least one required channel field must change")
        return self


class RequiredChannelResponse(BaseModel):
    id: int
    chat_id: str
    title: str
    invite_url: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupportTicketCreate(BaseModel):
    telegram_id: int = Field(gt=0)
    category: SupportCategory
    body: str | None = Field(default=None, max_length=3900)
    telegram_file_id: str | None = Field(default=None, max_length=512)
    file_type: SupportFileType | None = None

    @model_validator(mode="after")
    def message_is_not_empty(self):
        if not str(self.body or "").strip() and not str(self.telegram_file_id or "").strip():
            raise ValueError("Support request must contain text or an attachment")
        return self


class SupportReplyCreate(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    body: str = Field(min_length=1, max_length=3900)


class SupportTicketResponse(BaseModel):
    id: int
    category: SupportCategory
    status: Literal["open", "answered", "closed"]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    user: dict[str, Any]
    messages: list[dict[str, Any]]
    recipients: list[int] = Field(default_factory=list)

