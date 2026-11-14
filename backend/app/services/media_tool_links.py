"""Fetch only a short public-link sample, isolated from paid recognition."""
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.services.media_tool_providers import ToolError

LINK_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
    "instagram.com", "www.instagram.com", "m.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com",
    "threads.com", "www.threads.com", "threads.net", "www.threads.net",
}
MAX_SAMPLE_BYTES = 25 * 1024**2


def normalize_music_link(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("Invalid music link")
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or parts.hostname not in LINK_HOSTS
            or parts.username or parts.password or parts.port not in {None, 80, 443}):
        raise ValueError("Unsupported music link")
    # No local URLs, custom ports, arbitrary shorteners, or credentials.
    return urlunsplit(("https", parts.hostname, parts.path or "/", parts.query, ""))


def fetch_music_link(url, target, *, timeout=120):
    url = normalize_music_link(url)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if target.is_symlink() or target.exists():
        raise ToolError("input_expired")
    # yt-dlp may spawn FFmpeg. A process group ensures timeout/cancellation
    # kills both, before the Worker removes the attempt's temporary files.
    process = subprocess.Popen([sys.executable, "-m", "app.services.media_tool_links", url, str(target)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        code = process.wait(timeout=timeout)
    except BaseException as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise ToolError("link_timeout") from None
        raise
    finally:
        for path in target.parent.glob("link-source.*"):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
    if code or not target.is_file() or target.is_symlink() or not 0 < target.stat().st_size < MAX_SAMPLE_BYTES:
        target.unlink(missing_ok=True)
        raise ToolError("link_unavailable")
    return target


def download_link_sample(url, target):
    """Child process: existing extractor profile, first media item, 12 seconds."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func
    from app.services.download import _get_media_info_options
    from app.services.media_tool_providers import QuietLogger

    url = normalize_music_link(url)
    options = _get_media_info_options(url)
    options.update(
        skip_download=False, noplaylist=True, playlist_items="1", playlistend=1,
        format="bestaudio/best", outtmpl=str(target.parent / "link-source.%(ext)s"),
        download_ranges=download_range_func(None, [(0, 12)]),
        external_downloader="ffmpeg", external_downloader_args={
            "ffmpeg_i": ["-protocol_whitelist", "http,https,tcp,tls,crypto", "-rw_timeout", "15000000"],
            "ffmpeg_o": ["-fs", str(MAX_SAMPLE_BYTES), "-t", "12"],
        },
        allowed_extractors=["youtube.*", "instagram.*", "tiktok.*", "twitter.*", "threads.*"],
        socket_timeout=12, retries=0, fragment_retries=0, extractor_retries=0,
        cachedir=False, nopart=True, fixup="never", logger=QuietLogger(),
    )
    with YoutubeDL(options) as downloader:
        downloader.extract_info(url, download=True)
    files = [path for path in target.parent.glob("link-source.*")
             if path.is_file() and not path.is_symlink() and path.suffix not in {".part", ".ytdl", ".temp"}]
    if len(files) != 1 or not 0 < files[0].stat().st_size < MAX_SAMPLE_BYTES:
        raise ToolError("link_unavailable")
    files[0].replace(target)


if __name__ == "__main__":
    try:
        download_link_sample(sys.argv[1], Path(sys.argv[2]))
    except Exception:
        # Parent maps this to a fixed user-visible error, without upstream
        # URLs, cookies, headers, or provider secrets in process output.
        sys.exit(1)
