from functools import lru_cache

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

    free_daily_download_limit: int = 10
    free_max_file_size_mb: int = 100
    max_concurrent_downloads: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
