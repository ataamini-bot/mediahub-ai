"""At most three distinct 12-second fingerprints; no retry of service errors."""
import asyncio
import io
import json
import re
import time
import wave

import httpx
from shazamio import Shazam

from app.services.media_tool_providers import ToolError, recognition_sample

RECOGNITION_TIMEOUT = 45
RECOGNITION_BUDGET = 90
MAX_RESPONSE_BYTES = 2 * 1024**2


class RecognitionClient:
    """Replace the library's retrying client with a single bounded request."""

    def __init__(self, client):
        self.client = client

    async def request(self, method, url, **kwargs):
        target = httpx.URL(url)
        if (method != "POST" or target.scheme != "https" or target.host != "amp.shazam.com"
                or target.port not in (None, 443) or target.userinfo
                or not target.path.startswith("/discovery/v5/")):
            raise ToolError("provider_rejected")
        # ShazamIO passes proxy=None. Proxy/profile rotation and redirected
        # requests are not used to work around provider access restrictions.
        if kwargs.pop("proxy", None) is not None:
            raise ToolError("provider_rejected")
        from app.services.recognition_access import before_request, record_limit
        await asyncio.to_thread(before_request, "shazamio")
        async with self.client.stream(method, url, **kwargs) as response:
            if response.status_code == 429:
                await asyncio.to_thread(record_limit, "shazamio", response.headers)
                raise ToolError("provider_busy")
            if response.status_code in (401, 403):
                raise ToolError("provider_auth")
            if response.status_code != 200:
                raise ToolError("provider_rejected")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ToolError("invalid_provider_result")
                chunks.append(chunk)
        try:
            result = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            raise ToolError("invalid_provider_result") from None
        if not isinstance(result, dict):
            raise ToolError("invalid_provider_result")
        return result


def track_identity(response):
    if not isinstance(response, dict):
        raise ToolError("invalid_provider_result")
    matches, track = response.get("matches"), response.get("track")
    if "error" in response or "errors" in response or not isinstance(matches, list):
        raise ToolError("invalid_provider_result")
    if not matches and not track:
        raise ToolError("music_not_found")
    if not isinstance(matches, list) or not matches or not isinstance(track, dict):
        raise ToolError("invalid_provider_result")
    title, artist = track.get("title"), track.get("subtitle")
    if not isinstance(title, str) or not title.strip() or not isinstance(artist, str) or not artist.strip():
        raise ToolError("invalid_provider_result")
    # Shazam does not reliably supply full-track duration. Never treat the
    # sample/preview duration as the song's duration. Full-song selection uses
    # strict title/artist matching and the independent source's duration.
    identity = {"title": title.strip()[:200], "artist": artist.strip()[:200],
                "album": "", "duration": None}
    key = str(track.get("key") or "")
    if re.fullmatch(r"[0-9]{1,20}", key):
        identity.update(recognition_provider="shazamio", recognition_key=key)
    return identity


async def recognize_sample(sample):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10),
                                 trust_env=False, follow_redirects=False) as client:
        async with Shazam(http_client=RecognitionClient(client)) as shazam:
            return await shazam.recognize(sample)


def sample_windows(sample):
    """Separate signatures, never one 36-second signature or a repeated sample."""
    with wave.open(str(sample), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ToolError("invalid_audio")
        for start in (0, 12, 24):
            # Keep short original uploads supported, but do not make another
            # request for a short tail at the end of a longer clip.
            remaining = audio.getnframes() - start * 16000
            if remaining < (1 if start == 0 else 8) * 16000:
                break
            audio.setpos(start * 16000)
            frames = audio.readframes(12 * 16000)
            content = io.BytesIO()
            with wave.open(content, "wb") as part:
                part.setnchannels(1)
                part.setsampwidth(2)
                part.setframerate(16000)
                part.writeframes(frames)
            yield content.getvalue()


def recognize_shazam(source, *, check_cancelled=None):
    sample = source.parent / "sample.wav"
    deadline = time.monotonic() + RECOGNITION_BUDGET
    try:
        recognition_sample(source, sample, seconds=36)
        for content in sample_windows(sample):
            if check_cancelled:
                check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError("provider_uncertain")

            async def bounded():
                return await asyncio.wait_for(recognize_sample(content),
                    timeout=min(RECOGNITION_TIMEOUT, remaining))

            response = asyncio.run(bounded())
            try:
                return track_identity(response)
            except ToolError as exc:
                if exc.code != "music_not_found":
                    raise
        raise ToolError("music_not_found")
    except (TimeoutError, httpx.TimeoutException, httpx.TransportError):
        raise ToolError("provider_uncertain") from None
    except (ValueError, OSError, wave.Error, EOFError):
        raise ToolError("invalid_audio") from None
    finally:
        sample.unlink(missing_ok=True)
