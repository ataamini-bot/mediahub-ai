import hashlib
import os
import re
import urllib.parse
import urllib.request
from typing import Literal

import redis


NotificationTopic = Literal[
    "monitoring",
    "payments",
    "backups",
    "system",
]


TOPIC_ENV_NAMES = {
    "monitoring":
        "ADMIN_NOTIFICATIONS_MONITORING_TOPIC_ID",

    "payments":
        "ADMIN_NOTIFICATIONS_PAYMENTS_TOPIC_ID",

    "backups":
        "ADMIN_NOTIFICATIONS_BACKUPS_TOPIC_ID",

    "system":
        "ADMIN_NOTIFICATIONS_SYSTEM_TOPIC_ID",
}


def _env_enabled(
    name: str,
    default: bool = False,
) -> bool:

    value = os.getenv(
        name,
        "true" if default else "false",
    )

    return (
        value.strip().lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


def _sanitize_text(
    text: str,
) -> str:

    result = str(
        text
        or ""
    )

    # --------------------------------------------------------
    # Never send common secrets to the admin channel.
    # --------------------------------------------------------

    secret_patterns = [
        r"(?i)(sessionid\s*[=:]\s*)[^\s,&]+",
        r"(?i)(csrftoken\s*[=:]\s*)[^\s,&]+",
        r"(?i)(authorization\s*[=:]\s*)[^\s]+",
        r"(?i)(password\s*[=:]\s*)[^\s]+",
        r"(?i)(api[_-]?key\s*[=:]\s*)[^\s]+",
        r"(?i)(bot[_-]?token\s*[=:]\s*)[^\s]+",
    ]

    for pattern in secret_patterns:

        result = re.sub(
            pattern,
            r"\1[REDACTED]",
            result,
        )

    # Also redact the actual configured Telegram Bot token
    # if it accidentally appears in an exception message.

    bot_token = (
        os.getenv(
            "TELEGRAM_BOT_TOKEN",
            "",
        )
        .strip()
    )

    if bot_token:

        result = result.replace(
            bot_token,
            "[REDACTED_BOT_TOKEN]",
        )

    # Telegram message limit is 4096 characters.
    # Keep a small safety margin.

    if len(result) > 3900:

        result = (
            result[:3850]
            + "\n\n[message truncated]"
        )

    return result


def _get_default_dedup_seconds(
    topic: NotificationTopic,
) -> int:

    if topic != "monitoring":
        return 0

    raw_value = os.getenv(
        "ADMIN_NOTIFICATIONS_DEDUP_SECONDS",
        "600",
    ).strip()

    try:
        value = int(
            raw_value
        )

    except ValueError:
        value = 600

    return max(
        0,
        value,
    )


def _build_dedup_key(
    topic: NotificationTopic,
    text: str,
) -> str:

    safe_text = _sanitize_text(
        text
    )

    fingerprint = hashlib.sha256(
        (
            f"{topic}\n"
            f"{safe_text}"
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    return (
        "mediahub:"
        "admin-notifications:"
        "dedup:"
        f"{topic}:"
        f"{fingerprint}"
    )


def _claim_dedup_slot(
    topic: NotificationTopic,
    text: str,
    dedup_seconds: int,
) -> bool:

    if dedup_seconds <= 0:
        return True

    redis_url = (
        os.getenv(
            "REDIS_URL",
            "redis://redis:6379/0",
        )
        .strip()
    )

    try:

        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=3,
            socket_timeout=3,
        )

        claimed = client.set(
            _build_dedup_key(
                topic,
                text,
            ),
            "1",
            nx=True,
            ex=dedup_seconds,
        )

        return bool(
            claimed
        )

    except Exception as exc:

        # Fail open:
        # a Redis problem must never hide
        # an important admin notification.

        print(
            "Admin notification dedup failed: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return True


def _release_dedup_slot(
    topic: NotificationTopic,
    text: str,
    dedup_seconds: int,
) -> None:

    if dedup_seconds <= 0:
        return

    redis_url = (
        os.getenv(
            "REDIS_URL",
            "redis://redis:6379/0",
        )
        .strip()
    )

    try:

        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=3,
            socket_timeout=3,
        )

        client.delete(
            _build_dedup_key(
                topic,
                text,
            )
        )

    except Exception as exc:

        # Releasing the dedup slot is best-effort.
        # Never raise from notification cleanup.

        print(
            "Admin notification dedup release failed: "
            f"{type(exc).__name__}"
        )


def send_admin_notification(
    topic: NotificationTopic,
    text: str,
    *,
    silent: bool = False,
    dedup_seconds: int = 0,
) -> bool:

    if not _env_enabled(
        "ADMIN_NOTIFICATIONS_ENABLED"
    ):

        return False

    token = (
        os.getenv(
            "TELEGRAM_BOT_TOKEN",
            ""
        )
        .strip()
    )

    chat_id = (
        os.getenv(
            "ADMIN_NOTIFICATIONS_CHAT_ID",
            ""
        )
        .strip()
    )

    topic_env_name = (
        TOPIC_ENV_NAMES.get(
            topic
        )
    )

    if not topic_env_name:

        print(
            "Admin notification skipped: "
            f"unknown topic={topic}"
        )

        return False

    topic_id = (
        os.getenv(
            topic_env_name,
            ""
        )
        .strip()
    )

    if (
        not token
        or not chat_id
    ):

        print(
            "Admin notification skipped: "
            "Telegram configuration is incomplete"
        )

        return False

    base_url = (
        os.getenv(
            "ADMIN_NOTIFICATIONS_TELEGRAM_API_URL",
            "https://api.telegram.org",
        )
        .strip()
        .rstrip("/")
    )

    sanitized_text = _sanitize_text(
        text
    )

    if not _claim_dedup_slot(
        topic,
        sanitized_text,
        dedup_seconds,
    ):

        print(
            "Admin notification suppressed "
            "by dedup: "
            f"topic={topic}"
        )

        # The notification was intentionally
        # handled/suppressed, not failed.
        return True

    payload = {
        "chat_id":
            chat_id,

        "text":
            sanitized_text,

        "disable_notification":
            "true"
            if silent
            else "false",
    }

    if topic_id:

        payload[
            "message_thread_id"
        ] = topic_id

    data = (
        urllib.parse.urlencode(
            payload
        )
        .encode()
    )

    url = (
        f"{base_url}"
        f"/bot{token}"
        f"/sendMessage"
    )

    try:

        with urllib.request.urlopen(
            url,
            data=data,
            timeout=20,
        ) as response:

            if (
                response.status
                < 200
                or response.status
                >= 300
            ):

                _release_dedup_slot(
                    topic,
                    sanitized_text,
                    dedup_seconds,
                )

                print(
                    "Admin notification failed: "
                    f"HTTP {response.status}"
                )

                return False

    except Exception as exc:

        _release_dedup_slot(
            topic,
            sanitized_text,
            dedup_seconds,
        )

        safe_error = _sanitize_text(
            str(exc)
        )

        print(
            "Admin notification failed: "
            f"{type(exc).__name__}: "
            f"{safe_error}"
        )

        return False

    return True


def notify_monitoring(
    text: str,
    *,
    silent: bool = False,
) -> bool:

    return send_admin_notification(
        "monitoring",
        text,
        silent=silent,
    )


def notify_payment(
    text: str,
    *,
    silent: bool = False,
) -> bool:

    return send_admin_notification(
        "payments",
        text,
        silent=silent,
    )


def notify_backup(
    text: str,
    *,
    silent: bool = False,
) -> bool:

    return send_admin_notification(
        "backups",
        text,
        silent=silent,
    )


def notify_system(
    text: str,
    *,
    silent: bool = False,
) -> bool:

    return send_admin_notification(
        "system",
        text,
        silent=silent,
    )


NotificationLevel = Literal[
    "critical",
    "error",
    "warning",
    "recovery",
    "info",
]


LEVEL_STYLES = {
    "critical": (
        "🔴",
        "بحرانی",
    ),
    "error": (
        "❌",
        "خطا",
    ),
    "warning": (
        "⚠️",
        "هشدار",
    ),
    "recovery": (
        "🟢",
        "رفع مشکل",
    ),
    "info": (
        "ℹ️",
        "اطلاع",
    ),
}


def format_admin_notification(
    *,
    level: NotificationLevel,
    title: str,
    message: str,
    service: str | None = None,
    component: str | None = None,
    action: str | None = None,
) -> str:

    style = LEVEL_STYLES.get(
        level
    )

    if not style:

        raise ValueError(
            f"Unknown notification level: {level}"
        )

    emoji, level_title = style

    lines = [
        f"{emoji} {level_title}",
        "",
        f"📌 {title}",
    ]

    if service:

        lines.append(
            f"📦 سرویس: {service}"
        )

    if component:

        lines.append(
            f"🔧 بخش: {component}"
        )

    lines.extend([
        "",
        f"📝 {message}",
    ])

    if action:

        lines.extend([
            "",
            "💡 اقدام پیشنهادی:",
            action,
        ])

    return "\n".join(
        lines
    )


def notify_event(
    topic: NotificationTopic,
    *,
    level: NotificationLevel,
    title: str,
    message: str,
    service: str | None = None,
    component: str | None = None,
    action: str | None = None,
    silent: bool = False,
    dedup_seconds: int | None = None,
) -> bool:

    text = format_admin_notification(
        level=level,
        title=title,
        message=message,
        service=service,
        component=component,
        action=action,
    )

    if dedup_seconds is None:

        dedup_seconds = (
            _get_default_dedup_seconds(
                topic
            )
        )

    return send_admin_notification(
        topic,
        text,
        silent=silent,
        dedup_seconds=dedup_seconds,
    )
