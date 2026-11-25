"""Verify an otherwise ambiguous public song with the selected free provider."""
import asyncio
from pathlib import Path
import subprocess
import tempfile
import time

from app.services.music_identity import can_verify_audio


def matches_sample(track, sample, deadline, control):
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
        return identity.get("recognition_key") == track["recognition_key"]
