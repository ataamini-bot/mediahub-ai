from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AdminContextResponse(BaseModel):
    telegram_id: int
    user_id: int | None
    admin_account_id: int | None
    is_admin: bool
    is_superadmin: bool
    roles: list[str]
    permissions: list[str]


class ApplicationSettingUpdate(BaseModel):
    actor_telegram_id: int
    category: str = Field(min_length=2, max_length=80)
    value: Any
    is_sensitive: bool = False
    description: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class ApplicationSettingResponse(BaseModel):
    key: str
    category: str
    value: Any | None
    is_sensitive: bool
    is_configured: bool
    description: str | None
    version: int

    model_config = ConfigDict(from_attributes=True)
