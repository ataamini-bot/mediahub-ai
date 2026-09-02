import asyncio
from types import SimpleNamespace

from app.main import DownloadMessageFilter, download_error_text
from app.services.backend import BackendAPIError


def _matches_download_handler(text: str | None) -> bool:
    message = SimpleNamespace(text=text)
    return asyncio.run(DownloadMessageFilter()(message))


def test_download_filter_rejects_telegram_commands():
    for text in (
        "/admin",
        "/language",
        "/admin@MediaHubBot",
        "   /admin",
    ):
        assert _matches_download_handler(text) is False


def test_download_filter_keeps_normal_download_inputs():
    assert _matches_download_handler("https://example.com/video") is True
    assert _matches_download_handler("watch https://example.com/video") is True
    assert _matches_download_handler("") is False
    assert _matches_download_handler(None) is False


def test_plan_limit_errors_are_rendered_in_persian():
    quality_error = BackendAPIError(
        status_code=422,
        detail={
            "code": "download_quality_limit_exceeded",
            "plan_name": "Free",
            "max_quality": 720,
        },
    )
    daily_error = BackendAPIError(
        status_code=429,
        detail={
            "code": "daily_download_limit_reached",
            "plan_name": "Free",
            "used": 3,
            "limit": 3,
        },
    )

    assert "720p" in download_error_text(quality_error)
    assert "سهمیه روزانه" in download_error_text(daily_error)
    assert "3/3" in download_error_text(daily_error)
