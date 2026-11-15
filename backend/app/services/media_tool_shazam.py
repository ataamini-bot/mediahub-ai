"""Bounded ShazamIO recognition: one fingerprint request, no paid fallback."""
import asyncio
import json

import httpx
from shazamio import Shazam

from app.services.media_tool_providers import ToolError, recognition_sample

RECOGNITION_TIMEOUT = 45
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
        async with self.client.stream(method, url, **kwargs) as response:
            if response.status_code == 429:
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
    if not matches and not track:
        raise ToolError("music_not_found")
    if not isinstance(matches, list) or not matches or not isinstance(track, dict):
        raise ToolError("invalid_provider_result")
    title, artist = track.get("title"), track.get("subtitle")
    if not isinstance(title, str) or not title.strip() or not isinstance(artist, str) or not artist.strip():
        raise ToolError("invalid_provider_result")
    # Shazam does not reliably supply full-track duration. Never treat the
    # sample/preview duration as the song's duration or start an unverified download.
    # Missing duration keeps the existing explicit source-selection step.
    return {"title": title.strip()[:200], "artist": artist.strip()[:200],
            "album": "", "duration": None}


async def recognize_sample(sample):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10),
                                 trust_env=False, follow_redirects=False) as client:
        async with Shazam(http_client=RecognitionClient(client)) as shazam:
            return await shazam.recognize(sample)


def recognize_shazam(source):
    sample = source.parent / "sample.wav"
    try:
        recognition_sample(source, sample)

        async def bounded():
            return await asyncio.wait_for(recognize_sample(sample), timeout=RECOGNITION_TIMEOUT)

        response = asyncio.run(bounded())
        return track_identity(response)
    except (TimeoutError, httpx.TimeoutException, httpx.TransportError):
        raise ToolError("provider_uncertain") from None
    except (ValueError, OSError):
        raise ToolError("invalid_audio") from None
    finally:
        sample.unlink(missing_ok=True)
