"""Validated operations settings and cached notification routing."""
import json
import os
import time
from pathlib import Path

from sqlalchemy import select

from app.models.app_setting import ApplicationSetting
from app.services.application_settings import ApplicationSettingsService

TOPICS = ("monitoring", "payments", "backups", "system", "support")
MONITOR_DEFAULTS = {
    "interval_seconds": (30, 5, 300),
    "bot_heartbeat_seconds": (90, 45, 3600),
    "disk_percent": (85, 1, 100),
    "ram_percent": (90, 1, 100),
    "cpu_percent": (90, 1, 100),
    "celery_queue_size": (100, 1, 1000000),
    "download_error_percent": (25, 1, 100),
    "download_error_window_minutes": (15, 1, 1440),
}


def env_routes():
    def number(name):
        try:
            return int(os.getenv(name, "")) or None
        except ValueError:
            return None
    return {
        "enabled": os.getenv("ADMIN_NOTIFICATIONS_ENABLED", "false").lower() in {"true", "1", "yes"},
        "chat_id": number("ADMIN_NOTIFICATIONS_CHAT_ID"),
        "topics": {topic: number(f"ADMIN_NOTIFICATIONS_{topic.upper()}_TOPIC_ID") for topic in TOPICS},
    }


def resolve_routes(values):
    routes = env_routes()
    for key in ("enabled", "chat_id"):
        if values.get(f"notifications.{key}") is not None:
            routes[key] = values[f"notifications.{key}"]
    for topic in TOPICS:
        key = f"notifications.topic.{topic}"
        if key in values and values[key] is not None:
            routes["topics"][topic] = values[key] or None
    return routes


async def operation_values(session):
    result = await session.execute(select(ApplicationSetting).where(
        ApplicationSetting.category.in_(("notifications", "monitoring")),
        ApplicationSetting.is_sensitive.is_(False),
    ))
    return {row.key: row.value_json for row in result.scalars()}


async def notification_routes(session):
    return resolve_routes(await operation_values(session))


def validate_operation(key, value):
    if key == "notifications.enabled":
        if not isinstance(value, bool):
            raise ValueError("Enabled must be true or false")
    elif key == "notifications.chat_id":
        if isinstance(value, bool) or not isinstance(value, int) or value >= 0:
            raise ValueError("A negative Telegram group id is required")
    elif key.startswith("notifications.topic.") and key.rsplit(".", 1)[-1] in TOPICS:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**31:
            raise ValueError("Topic id must be zero or a positive integer")
    elif key.startswith("monitor.") and key[8:] in MONITOR_DEFAULTS:
        _, minimum, maximum = MONITOR_DEFAULTS[key[8:]]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"Value must be an integer between {minimum} and {maximum}")
    else:
        raise ValueError("Unknown operations setting")
    return value


class OperationsCache:
    """Keep the last successful DB read so DB outages remain reportable."""
    def __init__(self):
        self.values = {}
        self.loaded_at = 0.0
        self.path = Path(os.getenv("OPERATIONS_CACHE_PATH", "/app/monitor-data/operations.json"))
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                self.values = data
        except (OSError, ValueError):
            pass

    def read(self):
        if time.monotonic() - self.loaded_at < 30:
            return self.values
        self.loaded_at = time.monotonic()
        try:
            from app.db.worker_session import WorkerSessionLocal
            with WorkerSessionLocal() as session:
                rows = session.execute(select(ApplicationSetting).where(
                    ApplicationSetting.category.in_(("notifications", "monitoring")),
                    ApplicationSetting.is_sensitive.is_(False),
                )).scalars()
                self.values = {row.key: row.value_json for row in rows}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(f".{os.getpid()}.tmp")
                temp.write_text(json.dumps(self.values))
                temp.chmod(0o600)
                temp.replace(self.path)
            except OSError:
                pass
        except Exception:
            pass
        return self.values

    def routes(self):
        return resolve_routes(self.read())

    def thresholds(self):
        values = self.read()
        thresholds = {}
        for key, (default, minimum, maximum) in MONITOR_DEFAULTS.items():
            value = values.get(f"monitor.{key}", default)
            thresholds[key] = value if isinstance(value, int) and minimum <= value <= maximum else default
        return thresholds


operations_cache = OperationsCache()
