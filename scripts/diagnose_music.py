"""Compare bounded Shazam windows on one public link, without changing bot state.

Run inside the existing worker: python - URL < scripts/diagnose_music.py
No database writes, AudD requests, profile rotation or provider retries.
"""
import asyncio
from array import array
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import wave


_FETCH = r'''
import sys
from pathlib import Path
import yt_dlp
from yt_dlp.utils import download_range_func
from app.services.media_tool_links import download_link_sample

OriginalDownloader = yt_dlp.YoutubeDL
class ProbeDownloader(OriginalDownloader):
    def __init__(self, options):
        options = dict(options)
        options['download_ranges'] = download_range_func(None, [(0, 36)])
        args = dict(options['external_downloader_args'])
        args['ffmpeg_o'] = ['-fs', str(25 * 1024**2), '-t', '36']
        options['external_downloader_args'] = args
        super().__init__(options)
yt_dlp.YoutubeDL = ProbeDownloader
download_link_sample(sys.argv[1], Path(sys.argv[2]))
'''


def fetch(url, target):
    process = subprocess.Popen([sys.executable, "-c", _FETCH, url, str(target)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        code = process.wait(timeout=120)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    if code or not target.is_file() or not 0 < target.stat().st_size < 25 * 1024**2:
        raise RuntimeError("LINK_FETCH_FAILED")


def windows(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise RuntimeError("UNEXPECTED_AUDIO_FORMAT")
        duration = audio.getnframes() / 16000
        print("SOURCE_SECONDS=" + str(round(duration, 2)), flush=True)
        for start in (0, 12, 24):
            if duration - start < 5:
                continue
            audio.setpos(start * 16000)
            frames = audio.readframes(12 * 16000)
            samples = array("h", frames)
            if sys.byteorder != "little":
                samples.byteswap()
            rms = math.sqrt(sum(value * value for value in samples) / len(samples))
            level = round(20 * math.log10(rms / 32768), 1) if rms else None
            output = io.BytesIO()
            with wave.open(output, "wb") as part:
                part.setnchannels(1)
                part.setsampwidth(2)
                part.setframerate(16000)
                part.writeframes(frames)
            yield {"start_s": start, "seconds": round(len(samples) / 16000, 2),
                   "level_dbfs": level}, output.getvalue()


async def compare(path):
    from app.services.media_tool_shazam import recognize_sample
    from app.services.media_tool_providers import ToolError

    for index, (summary, content) in enumerate(windows(path)):
        if index:
            await asyncio.sleep(5)
        try:
            reply = await asyncio.wait_for(recognize_sample(content), timeout=35)
            matches, track = reply.get("matches"), reply.get("track")
            valid_track = isinstance(track, dict) and isinstance(track.get("title"), str)
            if "error" in reply or not isinstance(matches, list):
                summary.update(result="INVALID_REPLY", fields=sorted(reply)[:12])
            elif matches and valid_track:
                summary.update(result="MATCH", matches=len(matches),
                    title=track["title"][:200], artist=str(track.get("subtitle") or "")[:200])
            elif not matches and not track:
                summary.update(result="NO_MATCH", matches=0)
            else:
                summary.update(result="INVALID_REPLY", fields=sorted(reply)[:12])
        except Exception as error:
            # Raw exceptions can include CDN URLs, signatures or headers.
            summary.update(result="ERROR", error=error.code if isinstance(error, ToolError)
                           else type(error).__name__)
        print("WINDOW=" + json.dumps(summary, ensure_ascii=False), flush=True)
        # A rejection/rate limit/network failure is not a reason for more requests.
        if summary["result"] in {"ERROR", "INVALID_REPLY"}:
            break


def main():
    from app.services.media_tool_links import normalize_music_link

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python - PUBLIC_MEDIA_URL < diagnose_music.py")
    url = normalize_music_link(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="mediahub-music-check-") as directory:
        source, pcm = Path(directory) / "input", Path(directory) / "sample.wav"
        print("FETCHING_PUBLIC_SAMPLE=36_SECONDS_MAX", flush=True)
        fetch(url, source)
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,pipe", "-i", str(source), "-t", "36", "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(pcm)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=45, check=True)
        asyncio.run(compare(pcm))
    print("TEMP_FILES=REMOVED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("CHECK_FAILED=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None
