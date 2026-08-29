from typing import Literal
from urllib.parse import urlparse

from app.services.admin_notifications import (
    notify_event,
)


InstagramAuthIssue = Literal[
    "cookie_missing",
    "auth_rejected",
]


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
