"""Supported media conversion formats and validation helpers.

The bot exposes this same small, deliberately conservative set of containers
to users.  Keeping the allow-list here prevents an arbitrary FFmpeg muxer or
codec from being selected through the internal API.
"""

from __future__ import annotations


AUDIO_OUTPUT_FORMATS: tuple[str, ...] = (
    "mp3",
    "m4a",
    "wav",
    "aac",
    "flac",
    "ogg",
    "opus",
)

VIDEO_OUTPUT_FORMATS: tuple[str, ...] = (
    "mp4",
    "mkv",
    "avi",
    "mov",
    "webm",
)

OUTPUT_FORMATS = frozenset(AUDIO_OUTPUT_FORMATS + VIDEO_OUTPUT_FORMATS)


def normalize_output_format(value: object) -> str | None:
    """Return a canonical extension or ``None`` for an unsupported value."""

    normalized = str(value or "").strip().lower().lstrip(".")
    if normalized == "":
        return None
    return normalized if normalized in OUTPUT_FORMATS else None


def output_format_kind(value: object) -> str | None:
    """Return ``audio``/``video`` for a supported output extension."""

    normalized = normalize_output_format(value)
    if normalized is None:
        return None
    if normalized in AUDIO_OUTPUT_FORMATS:
        return "audio"
    return "video"


def is_supported_output_format(value: object) -> bool:
    return normalize_output_format(value) is not None
