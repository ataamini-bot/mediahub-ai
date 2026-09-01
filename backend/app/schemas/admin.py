from typing import Any

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class AdminRoleSummary(BaseModel):
    id: int
    code: str
    name: str
    is_system: bool
    is_active: bool


class AdminAccountResponse(BaseModel):
    account_id: int
    user_id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    is_superadmin: bool
    is_active: bool
    roles: list[AdminRoleSummary]
    created_at: datetime
    updated_at: datetime
    deactivated_at: datetime | None


class AdminAccountCreate(BaseModel):
    actor_telegram_id: int
    target_telegram_id: int
    role_codes: list[str] = Field(default_factory=list, max_length=50)
    is_superadmin: bool = False
    reason: str = Field(min_length=3, max_length=500)


class AdminAccountUpdate(BaseModel):
    actor_telegram_id: int
    role_codes: list[str] | None = Field(default=None, max_length=50)
    is_superadmin: bool | None = None
    is_active: bool | None = None
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def at_least_one_change(self):
        if (
            self.role_codes is None
            and self.is_superadmin is None
            and self.is_active is None
        ):
            raise ValueError("At least one administrator field must change")

        return self


class AdminPermissionResponse(BaseModel):
    id: int
    code: str
    description: str


class AdminRoleResponse(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    is_system: bool
    is_active: bool
    permission_codes: list[str]
    assignment_count: int
    created_at: datetime
    updated_at: datetime


class AdminRoleCreate(BaseModel):
    actor_telegram_id: int
    code: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    permission_codes: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


class AdminRoleUpdate(BaseModel):
    actor_telegram_id: int
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    permission_codes: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    is_active: bool | None = None
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def at_least_one_change(self):
        supplied = self.model_fields_set - {"actor_telegram_id", "reason"}

        if not supplied:
            raise ValueError("At least one role field must change")

        return self
