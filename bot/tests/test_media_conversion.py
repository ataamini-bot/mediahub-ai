from app.keyboards.conversion import (
    build_audio_format_keyboard,
    build_conversion_format_keyboard,
)
from app.keyboards.payment import build_home_keyboard, build_home_reply_keyboard
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
