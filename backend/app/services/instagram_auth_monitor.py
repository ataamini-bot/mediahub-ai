import os
from typing import Literal
from urllib.parse import urlparse

import redis

from app.services.admin_notifications import (
    notify_event,
)


InstagramAuthIssue = Literal[
    "cookie_missing",
    "auth_rejected",
]


INSTAGRAM_AUTH_INCIDENT_KEY = (
    "mediahub:"
    "instagram:"
    "story-auth:"
    "incident"
)


def _get_redis_client():
    return redis.Redis.from_url(
        os.getenv(
            "REDIS_URL",
            "redis://redis:6379/0",
        ),
        socket_connect_timeout=3,
        socket_timeout=3,
        decode_responses=True,
    )


def _mark_instagram_auth_incident(
    issue: InstagramAuthIssue,
) -> None:

    try:

        client = _get_redis_client()

        client.set(
            INSTAGRAM_AUTH_INCIDENT_KEY,
            issue,
        )

    except Exception as exc:

        print(
            "Instagram auth incident state "
            "update failed: "
            f"{type(exc).__name__}"
        )


def _is_instagram_story_url(
    source_url: str,
) -> bool:

    try:

        parsed = urlparse(
            source_url
        )

        hostname = (
            parsed.hostname
            or ""
        ).lower()

        path = (
            parsed.path
            or ""
        ).lower()

    except Exception:

        return False

    return (
        (
            hostname == "instagram.com"
            or hostname.endswith(
                ".instagram.com"
            )
        )
        and "/stories/" in path
    )


def classify_instagram_story_auth_error(
    *,
    source_url: str,
    error: Exception | str,
) -> InstagramAuthIssue | None:

    if not _is_instagram_story_url(
        source_url
    ):

        return None

    message = str(
        error
        or ""
    ).lower()

    # --------------------------------------------------------
    # Missing local authentication file
    # --------------------------------------------------------

    if (
        "instagram cookie file not found"
        in message
    ):

        return "cookie_missing"

    # --------------------------------------------------------
    # Normal Story/content failures must NOT trigger
    # an infrastructure/authentication alert.
    # --------------------------------------------------------

    ignored_patterns = (
        "requested instagram story was not found",
        "story was not found",
    )

    if any(
        pattern in message
        for pattern in ignored_patterns
    ):

        return None

    # --------------------------------------------------------
    # Instagram/yt-dlp authentication rejection
    # --------------------------------------------------------

    auth_patterns = (
        "unable to extract user info",
        "this content is unreachable",
        "use --cookies",
        "login required",
        "login_required",
        "not logged in",
        "challenge_required",
        "checkpoint_required",
    )

    if any(
        pattern in message
        for pattern in auth_patterns
    ):

        return "auth_rejected"

    return None


def notify_instagram_story_auth_issue(
    *,
    source_url: str,
    error: Exception | str,
    service: str,
) -> bool:

    issue = (
        classify_instagram_story_auth_error(
            source_url=source_url,
            error=error,
        )
    )

    if issue is None:
        return False

    _mark_instagram_auth_incident(
        issue
    )

    if issue == "cookie_missing":

        return notify_event(
            "monitoring",
            level="warning",
            title=(
                "فایل کوکی اینستاگرام "
                "در دسترس نیست"
            ),
            message=(
                "دریافت Instagram Story "
                "به دلیل نبودن فایل "
                "احراز هویت انجام نشد."
            ),
            service=service,
            component="Instagram Story",
            action=(
                "وجود و دسترسی فایل کوکی "
                "اینستاگرام روی سرور "
                "بررسی شود."
            ),
        )

    return notify_event(
        "monitoring",
        level="critical",
        title=(
            "نشست اینستاگرام نامعتبر است"
        ),
        message=(
            "Instagram احراز هویت لازم "
            "برای دریافت Story را رد کرد."
        ),
        service=service,
        component="Instagram Story",
        action=(
            "وضعیت Cookie/Session "
            "اینستاگرام بررسی و در صورت "
            "نیاز بروزرسانی شود."
        ),
    )



def notify_instagram_story_auth_recovery(
    *,
    source_url: str,
    service: str,
) -> bool:

    if not _is_instagram_story_url(
        source_url
    ):

        return False

    try:

        client = _get_redis_client()

        previous_issue = client.getdel(
            INSTAGRAM_AUTH_INCIDENT_KEY
        )

    except Exception as exc:

        print(
            "Instagram auth recovery state "
            "check failed: "
            f"{type(exc).__name__}"
        )

        return False

    if not previous_issue:
        return False

    sent = notify_event(
        "monitoring",
        level="recovery",
        title=(
            "اتصال اینستاگرام "
            "بازیابی شد"
        ),
        message=(
            "احراز هویت Instagram Story "
            "دوباره با موفقیت انجام شد."
        ),
        service=service,
        component="Instagram Story",
        action=(
            "اقدام دیگری لازم نیست. "
            "وضعیت سرویس به حالت عادی "
            "بازگشته است."
        ),
    )

    if sent:
        return True

    # Telegram delivery failed.
    # Restore the active incident so recovery
    # can be reported on the next successful Story.

    try:

        client.set(
            INSTAGRAM_AUTH_INCIDENT_KEY,
            previous_issue,
        )

    except Exception as exc:

        print(
            "Instagram auth recovery state "
            "restore failed: "
            f"{type(exc).__name__}"
        )

    return False
