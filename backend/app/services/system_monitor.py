import os
import threading

from app.services.admin_notifications import (
    notify_event,
)


def _get_system_dedup_seconds() -> int:

    raw_value = os.getenv(
        "ADMIN_NOTIFICATIONS_SYSTEM_DEDUP_SECONDS",
        "120",
    ).strip()

    try:
        value = int(raw_value)
    except ValueError:
        value = 120

    return max(
        0,
        value,
    )


def _send_system_event(
    *,
    level: str,
    title: str,
    message: str,
    service: str,
) -> None:

    notify_event(
        "system",
        level=level,
        title=title,
        message=message,
        service=service,
        component="System",
        dedup_seconds=(
            _get_system_dedup_seconds()
        ),
    )


def _dispatch_system_event(
    *,
    level: str,
    title: str,
    message: str,
    service: str,
    wait_seconds: float = 0.0,
) -> None:

    # Monitoring must never block application startup/shutdown
    # if Telegram or Redis is unavailable.

    thread = threading.Thread(
        target=_send_system_event,
        kwargs={
            "level": level,
            "title": title,
            "message": message,
            "service": service,
        },
        daemon=True,
        name=(
            f"mediahub-system-notify-"
            f"{service.lower()}"
        ),
    )

    thread.start()

    if wait_seconds > 0:

        thread.join(
            timeout=wait_seconds
        )


def notify_backend_started() -> None:

    _dispatch_system_event(
        level="info",
        title="Backend راه‌اندازی شد",
        message=(
            "سرویس Backend فعال است و "
            "درخواست‌های API را دریافت می‌کند."
        ),
        service="Backend",
    )


def notify_backend_stopping() -> None:

    _dispatch_system_event(
        level="info",
        title="Backend در حال توقف است",
        message=(
            "فرآیند خاموش‌سازی Backend "
            "آغاز شده است."
        ),
        service="Backend",
        wait_seconds=2.0,
    )


def notify_worker_ready() -> None:

    _dispatch_system_event(
        level="info",
        title="Worker راه‌اندازی شد",
        message=(
            "Worker فعال است و آماده "
            "دریافت Jobهای دانلود است."
        ),
        service="Worker",
    )


def notify_worker_stopping() -> None:

    _dispatch_system_event(
        level="info",
        title="Worker در حال توقف است",
        message=(
            "فرآیند خاموش‌سازی Worker "
            "آغاز شده است."
        ),
        service="Worker",
        wait_seconds=2.0,
    )
