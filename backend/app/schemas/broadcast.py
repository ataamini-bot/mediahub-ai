from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator


class BroadcastContent(BaseModel):
    kind: Literal["text", "photo", "video", "document", "forward"]
    text: str = Field(default="", max_length=4096)
    entities: list[dict] = Field(default_factory=list, max_length=100)
    file_id: str | None = Field(default=None, min_length=1, max_length=1024)
    source_chat_id: int | None = Field(default=None, gt=0)
    source_message_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_content(self):
        if self.kind == "forward":
            if not self.source_chat_id or not self.source_message_id or self.text or self.file_id or self.entities:
                raise ValueError("Forward requires only a source message")
        elif self.kind == "text":
            if not self.text.strip() or self.file_id:
                raise ValueError("Text required")
        elif not self.file_id:
            raise ValueError("Media file required")
        if self.kind != "forward" and (self.source_chat_id or self.source_message_id):
            raise ValueError("Unexpected source message")
        if len(self.text.encode("utf-16-le")) // 2 > (4096 if self.kind == "text" else 1024):
            raise ValueError("Message too long")
        return self


class BroadcastButton(BaseModel):
    text: str = Field(min_length=1, max_length=60)
    url: HttpUrl


class BroadcastCreate(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    request_id: UUID
    segment: Literal["all", "never_paid", "active", "plan", "former", "manual"]
    language: Literal["fa", "en", "all"]
    plan_id: int | None = Field(default=None, gt=0)
    telegram_ids: list[int] = Field(default_factory=list, max_length=1000)
    variants: dict[str, BroadcastContent]
    buttons: dict[str, BroadcastButton] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_audience(self):
        languages = {"fa", "en"} if self.language == "all" else {self.language}
        if set(self.variants) != languages or not set(self.buttons).issubset(languages):
            raise ValueError("Supply exactly one message for each selected language")
        if (self.segment == "plan") != (self.plan_id is not None):
            raise ValueError("Plan required only for a plan audience")
        self.telegram_ids = sorted(set(self.telegram_ids))
        if bool(self.telegram_ids) != (self.segment == "manual") or any(i <= 0 or i > 2**63 - 1 for i in self.telegram_ids):
            raise ValueError("Manual audience requires registered Telegram user IDs")
        for language, content in self.variants.items():
            if content.kind == "forward" and (content.source_chat_id != self.actor_telegram_id or language in self.buttons):
                raise ValueError("Forward must come from the administrator's private chat and cannot have added buttons")
        return self


class BroadcastActor(BaseModel):
    actor_telegram_id: int = Field(gt=0)


class BroadcastConfirm(BroadcastActor):
    confirmation_token: UUID


class BroadcastAck(BaseModel):
    claim_token: UUID
    outcome: Literal["sent", "blocked", "failed", "uncertain", "retry"]
    message_id: int | None = Field(default=None, gt=0)
    retry_after: int = Field(default=0, ge=0, le=86400)
    error_code: Literal["flood_wait", "forbidden", "chat_unavailable", "invalid_content", "transport_uncertain"] | None = None

    @model_validator(mode="after")
    def valid_result(self):
        if self.outcome == "sent" and self.message_id is None:
            raise ValueError("Delivered message ID required")
        if self.outcome == "retry" and (self.error_code != "flood_wait" or self.retry_after < 1):
            raise ValueError("Only a definite rate-limit rejection may retry automatically")
        return self
