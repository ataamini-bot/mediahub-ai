import re
from decimal import Decimal
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MediaHub AI"
    app_env: str = "development"
    debug: bool = True

    secret_key: str
    jwt_secret_key: str

    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    database_url: str

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str | None = None

    redis_url: str = "redis://redis:6379/0"

    telegram_bot_token: str
    telegram_superadmin_id: int | None = None
    telegram_admin_ids: str = ""
    bot_backend_api_key: str = ""
    data_encryption_key: str = ""

    payment_card_number: str = ""
    payment_card_holder: str = ""
    payment_bank_name: str = ""
    payment_price_1_month: Decimal = Decimal("0")
    payment_price_3_months: Decimal = Decimal("0")
    payment_price_6_months: Decimal = Decimal("0")
    payment_price_12_months: Decimal = Decimal("0")
    payment_receipt_max_size_mb: int = 10

    free_daily_download_limit: int = 3
    free_max_file_size_mb: int = 300
    max_concurrent_downloads: int = 3
    quota_timezone: str = "Asia/Tehran"

    @field_validator("telegram_superadmin_id", mode="before")
    @classmethod
    def empty_superadmin_id_is_none(cls, value):
        if value is None or str(value).strip() == "":
            return None

        return value

    @property
    def telegram_admin_id_set(
        self,
    ) -> frozenset[int]:
        """Return legacy bootstrap admins plus the primary superadmin.

        Runtime authorization is moving to database-backed RBAC.  This
        property remains during the migration so the existing payment
        review flow does not lose its configured administrators.
        """
        values = re.split(
            r"[\s,;]+",
            self.telegram_admin_ids.strip(),
        )

        try:
            admin_ids = {
                int(value)
                for value in values
                if value
            }

        except ValueError as exc:
            raise ValueError(
                "TELEGRAM_ADMIN_IDS must contain "
                "integer IDs separated by comma, "
                "semicolon, or whitespace"
            ) from exc

        if self.telegram_superadmin_id is not None:
            admin_ids.add(self.telegram_superadmin_id)

        return frozenset(admin_ids)

    @property
    def bootstrap_superadmin_id_set(self) -> frozenset[int]:
        """Return identities allowed to bootstrap database superadmins.

        The explicit singular value is preferred.  Legacy admin IDs are
        accepted until the installer and production environment migrate.
        """
        if self.telegram_superadmin_id is not None:
            return frozenset({self.telegram_superadmin_id})

        return self.telegram_admin_id_set

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
