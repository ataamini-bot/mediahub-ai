import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pathlib import Path

from app.keyboards.conversion import (
    build_audio_format_keyboard,
    build_conversion_format_keyboard,
)
from app.keyboards.payment import build_home_keyboard, build_home_reply_keyboard
from app.handlers.conversion import _download_telegram_file
from app.utils.media_conversion import (
    AUDIO_FORMATS,
    VIDEO_FORMATS,
    available_output_formats,
    format_label,
    normalize_format,
)


def _inline_buttons(keyboard):
    return [button for row in keyboard.inline_keyboard for button in row]


def test_conversion_format_allowlist_and_stream_aware_targets():
    assert normalize_format(".MP3") == "mp3"
    assert normalize_format("exe") is None
    assert available_output_formats(has_audio=True, has_video=False) == AUDIO_FORMATS
    assert available_output_formats(has_audio=True, has_video=True) == AUDIO_FORMATS + VIDEO_FORMATS
    assert available_output_formats(has_audio=False, has_video=True) == VIDEO_FORMATS
    assert available_output_formats(has_audio=False, has_video=False) == ()


def test_conversion_keyboards_use_bounded_callbacks_and_localized_labels():
    keyboard = build_conversion_format_keyboard(
        "abc123",
        has_audio=True,
        has_video=True,
        language="en",
    )
    buttons = _inline_buttons(keyboard)
    assert {button.callback_data for button in buttons[:-1]} >= {
        "convert:format:abc123:mp3",
        "convert:format:abc123:mp4",
    }
    assert all(len((button.callback_data or "").encode("utf-8")) <= 64 for button in buttons)
    assert format_label("mp3", "en") == "🎵 MP3 audio"
    assert buttons[-1].callback_data == "convert:cancel"

    audio_keyboard = build_audio_format_keyboard(
        "token",
        language="en",
        callback_prefix="audio:format",
        cancel_callback="audio:cancel:token",
    )
    assert audio_keyboard.inline_keyboard[-1][0].callback_data == "audio:cancel:token"
    assert all(
        (button.callback_data or "").startswith("audio:format:token:")
        for button in _inline_buttons(audio_keyboard)[:-1]
    )


def test_conversion_entry_is_present_in_both_home_menus():
    inline = _inline_buttons(build_home_keyboard("en"))
    assert any(
        button.callback_data == "convert:open" and button.text == "🔄 Convert media"
        for button in inline
    )

    reply = [button for row in build_home_reply_keyboard("en").keyboard for button in row]
    assert any(button.text == "🔄 Convert media" for button in reply)


def test_bot_can_read_uploads_exposed_by_the_local_telegram_api():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = compose_path.read_text(encoding="utf-8")

    assert "telegram_api_data:/var/lib/telegram-bot-api:ro" in compose


def test_local_bot_api_upload_is_copied_from_the_absolute_file_path(tmp_path):
    source = tmp_path / "telegram-api" / "voice.ogg"
    source.parent.mkdir()
    source.write_bytes(b"audio-bytes")
    destination = tmp_path / "downloads" / "incoming" / "upload.ogg"

    bot = SimpleNamespace(
        token="123:token",
        session=SimpleNamespace(
            api=SimpleNamespace(
                is_local=True,
                wrap_local_file=SimpleNamespace(to_local=lambda value: value),
            )
        ),
        get_file=AsyncMock(return_value=SimpleNamespace(file_path=str(source))),
        download_file=AsyncMock(side_effect=AssertionError("local path should be copied")),
    )

    source_kind = asyncio.run(_download_telegram_file(bot, "file-id", destination))

    assert source_kind == "local-path"
    assert destination.read_bytes() == b"audio-bytes"
    bot.download_file.assert_not_awaited()


def test_upload_reader_falls_back_to_bot_api_file_endpoint(tmp_path):
    destination = tmp_path / "downloads" / "incoming" / "upload.mp3"

    class Stream:
        def __init__(self):
            self.closed = False

        def __aiter__(self):
            async def chunks():
                yield b"audio-"
                yield b"bytes"

            return chunks()

        async def aclose(self):
            self.closed = True

    stream = Stream()
    session = SimpleNamespace(
        api=SimpleNamespace(
            is_local=True,
            wrap_local_file=SimpleNamespace(to_local=lambda value: value),
            file_url=lambda token, path: f"http://telegram-api/file/bot{token}/{path}",
        ),
        stream_content=lambda **_kwargs: stream,
    )
    bot = SimpleNamespace(
        token="123:token",
        session=session,
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="/missing/audio.mp3")),
        download_file=AsyncMock(side_effect=FileNotFoundError("not mounted")),
    )

    source_kind = asyncio.run(_download_telegram_file(bot, "file-id", destination))

    assert source_kind == "file-endpoint"
    assert destination.read_bytes() == b"audio-bytes"
    assert stream.closed is True
