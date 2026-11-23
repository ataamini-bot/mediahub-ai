import pytest

from app import main, runtime_config
from app.handlers import media_tools
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError
from app.tool_texts import TOOL_TEXTS


@pytest.mark.parametrize("language", ["fa", "en"])
@pytest.mark.parametrize("error", [
    BackendAPIError(status_code=503, detail={"code": "instagram_rate_limited", "retry_after": 900}),
    RuntimeError("instagram_rate_limited"),
])
def test_metadata_and_worker_throttling_are_localized(language, error, monkeypatch):
    monkeypatch.setattr(runtime_config, "_cache", {})
    token = ui_language.set(language)
    try:
        assert main.download_error_text(error) == TOOL_TEXTS["error_instagram_rate_limited"][language]
        assert main.download_error_markup(error) is None
        assert media_tools.friendly_error("instagram_rate_limited") == main.download_error_text(error)
    finally:
        ui_language.reset(token)


def test_rate_limit_text_remains_editable_from_admin(monkeypatch):
    key = "error_instagram_rate_limited"
    monkeypatch.setattr(runtime_config, "_cache", {"fa": (9999999999, {"tool_texts": {key: "لطفاً بعداً تلاش کنید."}})})
    token = ui_language.set("fa")
    try:
        assert main.download_error_text(RuntimeError("instagram_rate_limited")) == "لطفاً بعداً تلاش کنید."
        assert media_tools.friendly_error("instagram_rate_limited") == "لطفاً بعداً تلاش کنید."
    finally:
        ui_language.reset(token)
