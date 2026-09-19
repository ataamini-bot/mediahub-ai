import os


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token")
os.environ.setdefault("BOT_BACKEND_API_KEY", "a" * 64)


from app.keyboards.download import build_completed_download_keyboard
from app.keyboards.quality import build_quality_keyboard
from app.main import build_progress_text, _completed_file_caption
from app.middleware.interface import ui_language


def _buttons(keyboard):
    return [button for row in keyboard.inline_keyboard for button in row]


def test_progress_hides_internal_job_id_and_renders_media_stats():
    text = build_progress_text(
        428,
        "1080p",
        {
            "progress": 90,
            "downloaded_bytes": 34 * 1024 * 1024,
            "total_bytes": 38 * 1024 * 1024,
            "speed": 1.5 * 1024 * 1024,
            "eta": 3,
        },
        media_info={
            "formats": [
                {
                    "has_video": True,
                    "resolution": "1920x1080",
                    "fps": 30,
                }
            ]
        },
    )

    assert "Job ID" not in text
    assert "428" not in text
    assert "█████████░ 90%" in text
    assert "1080p • 30 FPS" in text
    assert "34 MB / 38 MB" in text
    assert "1.5 MB/s" in text
    assert "~3" in text


def test_delivered_file_exposes_id_only_through_details_button():
    token = ui_language.set("en")
    try:
        keyboard = build_completed_download_keyboard(428)
        callbacks = {
            button.callback_data
            for button in _buttons(keyboard)
        }
        labels = {
            button.callback_data: button.text
            for button in _buttons(keyboard)
        }
    finally:
        ui_language.reset(token)

    assert callbacks == {
        "download_again:428",
        "download_details:428",
    }
    assert labels == {
        "download_again:428": "🔄 Download again",
        "download_details:428": "📋 Details",
    }

    caption = _completed_file_caption(
        "MediaHub-428.mp4",
        "33 MB",
        {
            "id": 428,
            "quality": "1080p",
            "source_url": "https://www.instagram.com/reel/ABC/",
        },
        {
            "duration": 42,
            "formats": [
                {
                    "has_video": True,
                    "resolution": "1920x1080",
                    "fps": 30,
                }
            ],
        },
    )

    assert "Job ID" not in caption
    assert "MediaHub-428.mp4" in caption
    assert "1080p • 30 FPS" in caption
    assert "00:42" in caption
    assert "Instagram" in caption


def test_quality_keyboard_includes_audio_cover_and_description_actions():
    token = ui_language.set("en")
    try:
        keyboard = build_quality_keyboard(
            [(720, None)],
            token="abc123",
            audio_token="abc123",
            cover_token="abc123",
            description_token="abc123",
            language="en",
        )
    finally:
        ui_language.reset(token)

    callbacks = {
        button.callback_data
        for button in _buttons(keyboard)
    }

    assert {
        "audio:open:abc123",
        "media:cover:abc123",
        "media:description:abc123",
    } <= callbacks
    assert all(
        len((callback or "").encode("utf-8")) <= 64
        for callback in callbacks
    )
