"""Provider calls never return preview audio as a full song or retry a paid call."""
import json
import logging
import math
import re
import subprocess
import unicodedata
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def provider_json(url, *, token, files, data, timeout=45):
    data = {**data, "api_token": token}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=15), trust_env=False) as client:
            with client.stream("POST", url, data=data, files=files) as response:
                if response.status_code in (401, 403):
                    raise ToolError("provider_auth")
                if response.status_code == 429:
                    raise ToolError("provider_busy")
                if response.status_code >= 400:
                    raise ToolError("provider_rejected")
                chunks, size = [], 0
                for block in response.iter_bytes():
                    size += len(block)
                    if size > 24 * 1024**2:
                        raise ToolError("invalid_provider_result")
                    chunks.append(block)
                result = json.loads(b"".join(chunks))
                if not isinstance(result, dict):
                    raise ToolError("invalid_provider_result")
                return result
    except (httpx.TimeoutException, httpx.TransportError):
        # The provider may already have charged; never retry automatically.
        raise ToolError("provider_uncertain") from None
    except (ValueError, UnicodeError):
        raise ToolError("invalid_provider_result") from None


def recognition_sample(source, target):
    try:
        result = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,pipe", "-i", str(source), "-t", "12", "-vn", "-ac", "1",
            "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        if result.returncode or not target.is_file() or not 16000 <= target.stat().st_size <= 500000:
            raise ToolError("invalid_audio")
    except (OSError, subprocess.TimeoutExpired):
        raise ToolError("invalid_audio") from None


def recognize_music(source, token="", *, provider="shazamio"):
    # Never switch to a paid service after a free provider fails.
    if provider == "shazamio":
        from app.services.media_tool_shazam import recognize_shazam
        return recognize_shazam(source)
    if provider == "audd" and token:
        return recognize_audd(source, token)
    raise ToolError("provider_auth")


def recognize_audd(source, token):
    sample = source.parent / "sample.wav"
    recognition_sample(source, sample)
    try:
        with sample.open("rb") as audio:
            response = provider_json("https://api.audd.io/", token=token,
                files={"file": ("sample.wav", audio, "audio/wav")}, data={"return": "apple_music,spotify"})
        if response.get("status") != "success":
            raise ToolError("provider_rejected")
        track = response.get("result")
        if not track:
            raise ToolError("music_not_found")
        if not isinstance(track, dict) or not track.get("title") or not track.get("artist"):
            raise ToolError("invalid_provider_result")
        apple = track.get("apple_music") or {}
        spotify = track.get("spotify") or {}
        duration = spotify.get("duration_ms") or apple.get("durationInMillis")
        try:
            duration = float(duration) / 1000 if duration else None
            if duration is not None and (not math.isfinite(duration) or not 1 <= duration <= 3600):
                duration = None
        except (ValueError, TypeError):
            duration = None
        return {"title": str(track["title"])[:200], "artist": str(track["artist"])[:200],
                "album": str(track.get("album") or "")[:200], "duration": duration}
    finally:
        sample.unlink(missing_ok=True)


def words(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold().translate(str.maketrans("يك", "یک"))
    return set(re.findall(r"[^\W_]+", value))


def track_duration(value):
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and 30 <= duration <= 3600 else None


def song_candidates(track, entries):
    """Independent sources; missing reference duration requires user selection.

    No preview URLs, uploaded samples or arbitrary hosts.
    """
    expected = track_duration(track.get("duration"))
    wanted = words(track["title"])
    artist = words(track["artist"]) - {"the", "a", "an", "and", "feat", "ft", "official", "music"}
    variants = {"cover", "remix", "live", "karaoke", "slowed", "sped", "nightcore", "instrumental", "reaction",
                "preview", "teaser", "snippet", "shorts", "trailer", "loop"}
    matches, seen = [], set()
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen or item.get("is_live"):
            continue
        title = str(item.get("title") or "")
        title_words = words(title)
        context = title_words | words(item.get("channel") or item.get("uploader") or "")
        if not wanted or len(wanted & title_words) / len(wanted) < .8 or not artist or len(artist & context) / len(artist) < .6:
            continue
        if (title_words & variants) - wanted:
            continue
        try:
            duration = float(item.get("duration") or 0)
        except (TypeError, ValueError):
            continue
        # A source without duration cannot be distinguished from a teaser.
        if not 30 <= duration <= 3600:
            continue
        if not math.isfinite(duration):
            continue
        if expected and (abs(duration - expected) > max(15, expected * .10) or duration < expected * .9):
            continue
        if not expected and duration < 60:
            continue
        seen.add(video_id)
        matches.append({"title": title[:200], "channel": str(item.get("channel") or item.get("uploader") or "")[:150],
                        "duration": int(duration), "url": f"https://www.youtube.com/watch?v={video_id}"})
    return matches[:3]


def automatic_song_candidate(track, candidates):
    """Auto-download only one unambiguous, strict title/artist/duration match.

    Search order is never enough. Missing reference duration, loose matches or
    multiple equally plausible sources leave a clearly labelled user choice.
    """
    expected = track_duration(track.get("duration"))
    if not expected:
        return None
    wanted, artist = words(track.get("title", "")), words(track.get("artist", ""))
    matches = []
    for index, candidate in enumerate(candidates):
        duration = track_duration(candidate.get("duration"))
        title = words(candidate.get("title", ""))
        context = title | words(candidate.get("channel", ""))
        if (wanted and artist and wanted <= title and artist <= context and duration
                and abs(duration - expected) <= max(5, expected * .03)):
            matches.append(index)
    return matches[0] if len(matches) == 1 else None


class QuietLogger:
    def debug(self, *args):
        pass
    warning = error = debug


def find_full_tracks(track):
    from yt_dlp import YoutubeDL
    from app.services.download import _get_media_info_options
    options = _get_media_info_options("https://www.youtube.com/")
    options.update(extract_flat="in_playlist", skip_download=True, noplaylist=False,
                   playlistend=6, socket_timeout=12, retries=0, extractor_retries=0, logger=QuietLogger())
    query = " ".join((track["artist"], track["title"], "official audio"))
    try:
        with YoutubeDL(options) as downloader:
            found = downloader.extract_info("ytsearch6:" + query, download=False)
        return song_candidates(track, (found or {}).get("entries") or [])
    except Exception as exc:
        # Keep the identification even when YouTube is unavailable on this server.
        logger.warning("Music source search failed: error=%s", type(exc).__name__)
        return []
