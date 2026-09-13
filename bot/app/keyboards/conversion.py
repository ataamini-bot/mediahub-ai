from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.media_conversion import (
    AUDIO_FORMATS,
    VIDEO_FORMATS,
    format_label,
)


def build_conversion_format_keyboard(
    token: str,
    *,
    has_audio: bool,
    has_video: bool,
    language: str = "fa",
    callback_prefix: str = "convert:format",
    cancel_callback: str = "convert:cancel",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    formats = []
    if has_audio:
        formats.extend(AUDIO_FORMATS)
    if has_video:
        formats.extend(VIDEO_FORMATS)
    for index in range(0, len(formats), 2):
        rows.append([
            InlineKeyboardButton(
                text=format_label(fmt, language),
                callback_data=f"{callback_prefix}:{token}:{fmt}",
            )
            for fmt in formats[index:index + 2]
        ])
    rows.append([
        InlineKeyboardButton(
            text="❌ Cancel" if language == "en" else "❌ انصراف",
            callback_data=cancel_callback,
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_audio_format_keyboard(
    token: str,
    *,
    language: str = "fa",
    callback_prefix: str = "convert:format",
    cancel_callback: str = "convert:cancel",
) -> InlineKeyboardMarkup:
    return build_conversion_format_keyboard(
        token,
        has_audio=True,
        has_video=False,
        language=language,
        callback_prefix=callback_prefix,
        cancel_callback=cancel_callback,
    )
