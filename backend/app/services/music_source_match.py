"""Verify an otherwise ambiguous public song with the selected free provider."""
import asyncio
import logging
from pathlib import Path
import subprocess
import tempfile
import time

from app.services.music_identity import can_verify_audio
from app.services.musicfa import _identity_matches

logger = logging.getLogger(__name__)


def matches_sample(track, sample, deadline, control, *, require_key=False):
    if not can_verify_audio(track):
        return False
    from app.services.media_tool_shazam import recognize_sample, track_identity
    from app.services.media_tool_providers import ToolError

    control()
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        return False
    with tempfile.TemporaryDirectory(prefix="mediahub-source-match-") as directory:
        pcm = Path(directory) / "sample.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                        "-i", str(sample), "-t", "12", "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(pcm)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                       timeout=min(5, remaining))
        control()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not 16000 <= pcm.stat().st_size <= 500000:
            return False

        async def recognize():
            return await asyncio.wait_for(recognize_sample(pcm.read_bytes()), timeout=min(8, remaining))

        try:
            identity = track_identity(asyncio.run(recognize()))
        except ToolError as error:
            if error.code != "music_not_found":
                raise
            control()
            return False
        control()
        # The provider can return different catalogue keys for the same named
        # recording. This identity is from the source AUDIO, not its filename.
        # Keep the full version label when comparing names; discovery's relaxed
        # title must never let a live/remix/studio version replace this one.
        same_key = identity.get("recognition_key") == track["recognition_key"]
        same_names = _identity_matches(track, identity["artist"], identity["title"])
        matched = same_key if require_key else same_key or same_names
        logger.warning("Music source audio match: matched=%s, key_match=%s, name_match=%s",
                       matched, same_key, same_names)
        return matched
