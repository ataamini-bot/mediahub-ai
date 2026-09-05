from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.application_settings import (
    ApplicationSettingsService,
    SettingValidationError,
)


SettingKind = Literal["boolean", "integer", "timezone", "string_map"]


@dataclass(frozen=True, slots=True)
class ManagedSetting:
    key: str
    category: str
    kind: SettingKind
    default: Any
    label_fa: str
    description_fa: str
    minimum: int | None = None
    maximum: int | None = None
    allowed_keys: frozenset[str] | None = None
    value_max_length: int | None = None


CONTENT_KEYS = frozenset(
    {
        "welcome_title",
        "welcome_instruction",
        "tutorial",
        "faq",
        "support_intro",
        "support_prompt",
        "support_sent",
        "forced_join",
        "membership_verified",
    }
)
BUTTON_KEYS = frozenset(
    {
        "buy",
        "subscription",
        "language",
        "support",
        "tutorial",
        "faq",
        "admin",
        "check_membership",
        "back_home",
    }
)


MANAGED_SETTINGS: dict[str, ManagedSetting] = {
    "bot.maintenance_mode": ManagedSetting(
        key="bot.maintenance_mode",
        category="bot",
        kind="boolean",
        default=False,
        label_fa="حالت تعمیرات",
        description_fa="جلوگیری موقت از شروع دانلود و خرید جدید",
    ),
    "downloads.enabled": ManagedSetting(
        key="downloads.enabled",
        category="downloads",
        kind="boolean",
        default=True,
        label_fa="دانلود برای کاربران",
        description_fa="اجازه بررسی لینک و ایجاد دانلود جدید",
    ),
    "payments.enabled": ManagedSetting(
        key="payments.enabled",
        category="payments",
        kind="boolean",
        default=True,
        label_fa="خرید اشتراک",
        description_fa="اجازه شروع خرید و ارسال رسید جدید",
    ),
    "payments.receipt_max_size_mb": ManagedSetting(
        key="payments.receipt_max_size_mb",
        category="payments",
        kind="integer",
        default=10,
        minimum=1,
        maximum=50,
        label_fa="حداکثر حجم رسید",
        description_fa="حداکثر حجم تصویر یا PDF رسید به مگابایت",
    ),
    "quota.timezone": ManagedSetting(
        key="quota.timezone",
        category="quota",
        kind="timezone",
        default="Asia/Tehran",
        label_fa="منطقه زمانی سهمیه",
        description_fa="مرز نیمه‌شب برای بازنشانی سهمیه روزانه",
    ),
    "bot.content.fa": ManagedSetting(
        key="bot.content.fa",
        category="bot_content",
        kind="string_map",
        default={},
        label_fa="متن‌های فارسی کاربران",
        description_fa="متن صفحه شروع، آموزش، سوالات متداول و پشتیبانی",
        allowed_keys=CONTENT_KEYS,
        value_max_length=3900,
    ),
    "bot.content.en": ManagedSetting(
        key="bot.content.en",
        category="bot_content",
        kind="string_map",
        default={},
        label_fa="متن‌های انگلیسی کاربران",
        description_fa="English start, help, FAQ and support text",
        allowed_keys=CONTENT_KEYS,
        value_max_length=3900,
    ),
    "bot.buttons.fa": ManagedSetting(
        key="bot.buttons.fa",
        category="bot_buttons",
        kind="string_map",
        default={},
        label_fa="عنوان فارسی دکمه‌ها",
        description_fa="عنوان همه دکمه‌های اصلی کاربران",
        allowed_keys=BUTTON_KEYS,
        value_max_length=64,
    ),
    "bot.buttons.en": ManagedSetting(
        key="bot.buttons.en",
        category="bot_buttons",
        kind="string_map",
        default={},
        label_fa="عنوان انگلیسی دکمه‌ها",
        description_fa="English labels for every main user button",
        allowed_keys=BUTTON_KEYS,
        value_max_length=64,
    ),
}


def validate_managed_setting(key: str, value: Any) -> Any:
    definition = MANAGED_SETTINGS.get(key)
    if definition is None:
        return value

    if definition.kind == "boolean":
        if not isinstance(value, bool):
            raise SettingValidationError("Setting value must be boolean")
        return value

    if definition.kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingValidationError("Setting value must be an integer")
        if definition.minimum is not None and value < definition.minimum:
            raise SettingValidationError("Setting value is below minimum")
        if definition.maximum is not None and value > definition.maximum:
            raise SettingValidationError("Setting value is above maximum")
        return value

    if definition.kind == "timezone":
        normalized = str(value or "").strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise SettingValidationError("Unknown IANA timezone") from exc
        return normalized

    if definition.kind == "string_map":
        if not isinstance(value, dict):
            raise SettingValidationError("Setting value must be an object")
        allowed_keys = definition.allowed_keys or frozenset()
        if not set(value).issubset(allowed_keys):
            raise SettingValidationError("Setting contains an unknown text key")
        maximum = definition.value_max_length or 3900
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_value, str):
                raise SettingValidationError("Text setting values must be strings")
            rendered = raw_value.strip()
            if not rendered:
                raise SettingValidationError("Text setting values cannot be blank")
            if len(rendered) > maximum:
                raise SettingValidationError("Text setting value is too long")
            normalized[str(raw_key)] = rendered
        return normalized

    raise SettingValidationError("Unsupported managed setting type")


async def get_managed_setting(
    session: AsyncSession,
    key: str,
) -> Any:
    definition = MANAGED_SETTINGS[key]
    value = await ApplicationSettingsService(session).get_value(
        key,
        definition.default,
    )
    try:
        return validate_managed_setting(key, value)
    except SettingValidationError:
        return definition.default


async def get_receipt_max_size_mb(session: AsyncSession) -> int:
    value = await get_managed_setting(
        session,
        "payments.receipt_max_size_mb",
    )
    return int(value)


class PublicOperationDisabled(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


async def ensure_public_operation(
    session: AsyncSession,
    operation: Literal["downloads", "payments"],
) -> None:
    if await get_managed_setting(session, "bot.maintenance_mode"):
        raise PublicOperationDisabled(
            "maintenance_mode",
            "The bot is temporarily in maintenance mode",
        )

    enabled_key = f"{operation}.enabled"
    if not await get_managed_setting(session, enabled_key):
        raise PublicOperationDisabled(
            f"{operation}_disabled",
            f"New {operation} are temporarily disabled",
        )
