"""Client-side helpers for the upload-to-conversion flow."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


AUDIO_FORMATS: tuple[str, ...] = (
    "mp3",
    "m4a",
    "wav",
    "aac",
    "flac",
    "ogg",
    "opus",
)

VIDEO_FORMATS: tuple[str, ...] = (
    "mp4",
    "mkv",
    "avi",
    "mov",
    "webm",
)

SUPPORTED_FORMATS = frozenset(AUDIO_FORMATS + VIDEO_FORMATS)

FORMAT_LABELS = {
    "mp3": "MP3",
    "m4a": "M4A",
    "wav": "WAV",
    "aac": "AAC",
    "flac": "FLAC",
    "ogg": "OGG",
    "opus": "OPUS",
    "mp4": "MP4",
    "mkv": "MKV",
    "avi": "AVI",
    "mov": "MOV",
    "webm": "WEBM",
}


def normalize_format(value: object) -> str | None:
    normalized = str(value or "").strip().lower().lstrip(".")
    return normalized if normalized in SUPPORTED_FORMATS else None


def format_label(value: object, language: str = "fa") -> str:
    normalized = normalize_format(value) or ""
    label = FORMAT_LABELS.get(normalized, normalized.upper())
    if normalized in AUDIO_FORMATS:
        return f"🎵 {label}" if language != "en" else f"🎵 {label} audio"
    return f"🎬 {label}" if language != "en" else f"🎬 {label} video"


def probe_media_file(path: Path) -> dict | None:
    """Return stream facts used to build a safe target-format menu."""

    if not path.is_file():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    streams = payload.get("streams")
    if not isinstance(streams, list):
        streams = []
    has_audio = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in streams
    )
    has_video = any(
        isinstance(stream, dict) and stream.get("codec_type") == "video"
        for stream in streams
    )
    if not has_audio and not has_video:
        return None
    return {
        "has_audio": has_audio,
        "has_video": has_video,
        "duration": (payload.get("format") or {}).get("duration")
        if isinstance(payload.get("format"), dict)
        else None,
    }


def available_output_formats(*, has_audio: bool, has_video: bool) -> tuple[str, ...]:
    """Expose only targets that the input streams can actually produce."""

    if has_video:
        return (AUDIO_FORMATS if has_audio else ()) + VIDEO_FORMATS
    if has_audio:
        return AUDIO_FORMATS
    return ()
