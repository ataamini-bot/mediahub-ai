"""Opt-in provider configuration. Keys stay encrypted and are never returned."""
from pydantic import BaseModel, Field, SecretStr
from typing import Literal
from sqlalchemy import select

from app.models.app_setting import ApplicationSetting
from app.services.application_settings import ApplicationSettingsService

KINDS = ("music", "portrait")
DEFAULTS = {"enabled": False, "per_user_daily": 2, "global_daily": 30,
            "model": "gpt-image-2.6-flare"}


class ToolConfiguration(BaseModel):
    kind: Literal["music", "portrait"]
    enabled: bool
    per_user_daily: int = Field(default=2, ge=1, le=100)
    global_daily: int = Field(default=30, ge=1, le=10000)
    token: SecretStr | None = None
    model: Literal["gpt-image-2.6-flare", "gpt-image-2.6-sunburst", "gpt-image-2"] = "gpt-image-2.6-flare"


class PlanToolLimit(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    limit: int = Field(ge=-1, le=10000)
    period: Literal["daily", "weekly"] = "daily"


def validate_plan_tool_limits(value):
    if not isinstance(value, dict) or not set(value) <= set(KINDS):
        raise ValueError("Unknown media tool limit")
    return {kind: PlanToolLimit.model_validate(limit).model_dump() if limit is not None else None
            for kind, limit in value.items()}


async def read_config(db, kind):
    service = ApplicationSettingsService(db)
    config = {**DEFAULTS, **(await service.get_value(f"tools.{kind}.config", {}) or {})}
    config["token"] = await service.get_value(f"tools.{kind}.token", "")
    return config


def read_worker_config(db, kind):
    rows = db.scalars(select(ApplicationSetting).where(ApplicationSetting.key.in_(
        [f"tools.{kind}.config", f"tools.{kind}.token"]))).all()
    values = {row.key: ApplicationSettingsService.decrypt_value(row.encrypted_value)
              if row.is_sensitive and row.encrypted_value else row.value_json for row in rows}
    return {**DEFAULTS, **(values.get(f"tools.{kind}.config") or {}),
            "token": values.get(f"tools.{kind}.token") or ""}


def public_config(config):
    return {**{k: config[k] for k in DEFAULTS}, "configured": bool(config.get("token")),
            "available": bool(config.get("enabled") and config.get("token"))}


async def configure_tool(db, payload, user):
    data = ToolConfiguration.model_validate(payload)
    service = ApplicationSettingsService(db)
    token = data.token.get_secret_value().strip() if data.token is not None else None
    if token is not None:
        if len(token) > 512 or any(c.isspace() or ord(c) < 32 for c in token):
            raise ValueError("Invalid provider key")
        await service.set_value(key=f"tools.{data.kind}.token", category="tools", value=token,
            is_sensitive=True, actor_user_id=user.id, actor_telegram_id=user.telegram_id)
    previous = await read_config(db, data.kind)
    if data.enabled and not previous.get("token"):
        raise ValueError("A provider key is required before enabling this tool")
    await service.set_value(key=f"tools.{data.kind}.config", category="tools",
        value=data.model_dump(exclude={"kind", "token"}), is_sensitive=False,
        actor_user_id=user.id, actor_telegram_id=user.telegram_id)
    return public_config(await read_config(db, data.kind))
