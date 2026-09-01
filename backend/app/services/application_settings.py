import json
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.app_setting import ApplicationSetting
from app.services.audit import AuditService


SETTING_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,149}$")
CATEGORY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")


class SettingNotFound(LookupError):
    pass


class SettingConflict(RuntimeError):
    pass


class SettingValidationError(ValueError):
    pass


class SettingEncryptionError(RuntimeError):
    pass


class ApplicationSettingsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def normalize_key(value: str) -> str:
        normalized = str(value or "").strip().lower()

        if not SETTING_KEY_PATTERN.fullmatch(normalized):
            raise SettingValidationError("Invalid application setting key")

        return normalized

    @staticmethod
    def normalize_category(value: str) -> str:
        normalized = str(value or "").strip().lower()

        if not CATEGORY_PATTERN.fullmatch(normalized):
            raise SettingValidationError("Invalid application setting category")

        return normalized

    @staticmethod
    def _cipher() -> Fernet:
        encryption_key = settings.data_encryption_key.strip()

        if not encryption_key or encryption_key == "CHANGE_ME":
            raise SettingEncryptionError(
                "DATA_ENCRYPTION_KEY is not securely configured"
            )

        try:
            return Fernet(encryption_key.encode("ascii"))
        except (TypeError, ValueError) as exc:
            raise SettingEncryptionError(
                "DATA_ENCRYPTION_KEY is not a valid Fernet key"
            ) from exc

    @classmethod
    def encrypt_value(cls, value: Any) -> str:
        try:
            payload = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SettingValidationError(
                "Application setting value must be JSON serializable"
            ) from exc

        return cls._cipher().encrypt(payload).decode("ascii")

    @classmethod
    def decrypt_value(cls, encrypted_value: str) -> Any:
        try:
            payload = cls._cipher().decrypt(encrypted_value.encode("ascii"))
            return json.loads(payload.decode("utf-8"))
        except (InvalidToken, UnicodeError, json.JSONDecodeError) as exc:
            raise SettingEncryptionError(
                "Encrypted application setting cannot be decrypted"
            ) from exc

    async def list_settings(
        self,
        *,
        category: str | None = None,
    ) -> list[ApplicationSetting]:
        statement = select(ApplicationSetting).order_by(
            ApplicationSetting.category,
            ApplicationSetting.key,
        )

        if category is not None:
            statement = statement.where(
                ApplicationSetting.category
                == self.normalize_category(category)
            )

        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_setting(self, key: str) -> ApplicationSetting:
        normalized_key = self.normalize_key(key)
        result = await self.session.execute(
            select(ApplicationSetting).where(
                ApplicationSetting.key == normalized_key
            )
        )
        setting = result.scalar_one_or_none()

        if setting is None:
            raise SettingNotFound("Application setting not found")

        return setting

    async def get_value(self, key: str, default: Any = None) -> Any:
        try:
            setting = await self.get_setting(key)
        except SettingNotFound:
            return default

        if setting.is_sensitive:
            if not setting.encrypted_value:
                return default
            return self.decrypt_value(setting.encrypted_value)

        return setting.value_json

    async def set_value(
        self,
        *,
        key: str,
        category: str,
        value: Any,
        is_sensitive: bool,
        actor_user_id: int,
        actor_telegram_id: int,
        description: str | None = None,
        expected_version: int | None = None,
    ) -> ApplicationSetting:
        normalized_key = self.normalize_key(key)
        normalized_category = self.normalize_category(category)

        # Validate JSON even for public settings before touching the row.
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SettingValidationError(
                "Application setting value must be JSON serializable"
            ) from exc

        result = await self.session.execute(
            select(ApplicationSetting)
            .where(ApplicationSetting.key == normalized_key)
            .with_for_update()
        )
        setting = result.scalar_one_or_none()

        if setting is None:
            if expected_version is not None:
                raise SettingConflict("Application setting version changed")

            setting = ApplicationSetting(
                key=normalized_key,
                category=normalized_category,
                version=1,
            )
            self.session.add(setting)
        else:
            if (
                expected_version is not None
                and setting.version != expected_version
            ):
                raise SettingConflict("Application setting version changed")

            setting.version += 1

        setting.category = normalized_category
        setting.is_sensitive = is_sensitive
        setting.description = (
            str(description).strip()[:2000] if description else None
        )
        setting.updated_by_user_id = actor_user_id

        if is_sensitive:
            setting.value_json = None
            setting.encrypted_value = self.encrypt_value(value)
        else:
            setting.value_json = value
            setting.encrypted_value = None

        await self.session.flush()
        AuditService(self.session).record(
            action="setting.updated",
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="application_setting",
            target_id=setting.id,
            details={
                "key": normalized_key,
                "category": normalized_category,
                "is_sensitive": is_sensitive,
                "version": setting.version,
            },
        )
        return setting
