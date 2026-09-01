import asyncio
from types import SimpleNamespace

from app.main import DownloadMessageFilter


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
