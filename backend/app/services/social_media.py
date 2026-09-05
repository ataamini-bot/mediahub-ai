"""Public-only extraction helpers for social-media image posts and Threads.

The main download service deliberately keeps yt-dlp as the video extractor.
This module fills the gaps where a platform exposes image posts (which yt-dlp
does not treat as downloadable video) or, in Threads' case, has no yt-dlp
extractor at all.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


GALLERY_DL_TIMEOUT_SECONDS = 90
THREADS_REQUEST_TIMEOUT = (15, 30)
THREADS_MAX_HTML_BYTES = 2 * 1024 * 1024

LINK_PREVIEW_USER_AGENT = (
    "facebookexternalhit/1.1 "
    "(+http://www.facebook.com/externalhit_uatext.php)"
)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m3u8"}


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _host_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith("." + domain)


def social_platform(source_url: str) -> str | None:
    """Return the supported public-image platform for a source URL."""

    hostname = _hostname(source_url)
    if not hostname:
        return None

    if hostname == "pin.it" or hostname.startswith("pinterest.") or \
            ".pinterest." in hostname:
        return "pinterest"

    if _host_matches(hostname, "tiktok.com"):
        return "tiktok"

    if _host_matches(hostname, "threads.com") or \
            _host_matches(hostname, "threads.net"):
        return "threads"

    if _host_matches(hostname, "facebook.com") or hostname == "fb.watch":
        return "facebook"

    return None


def is_threads_url(source_url: str) -> bool:
    return social_platform(source_url) == "threads"


def _is_allowed_tiktok_cdn(hostname: str) -> bool:
    # TikTok uses regional forms such as tiktokcdn-us.com and
    # tiktokcdn-eu.com in addition to the base tiktokcdn.com domain.
    if re.fullmatch(
        r"(?:[a-z0-9-]+\.)*[a-z0-9-]*tiktokcdn(?:-[a-z0-9-]+)?\.com",
        hostname,
    ):
        return True

    return any(
        _host_matches(hostname, domain)
        for domain in ("byteimg.com", "ibytedtos.com")
    )


def is_allowed_social_media_url(
    platform: str,
    media_url: str,
    media_type: str = "image",
) -> bool:
    """Validate extractor output before it is probed or downloaded."""

    try:
        parsed = urlparse(media_url)
    except Exception:
        return False

    if parsed.scheme != "https" or not parsed.hostname:
        return False

    hostname = parsed.hostname.lower().rstrip(".")

    if platform == "pinterest":
        return _host_matches(hostname, "pinimg.com")

    if platform == "tiktok":
        return _is_allowed_tiktok_cdn(hostname)

    if platform == "facebook":
        return _host_matches(hostname, "fbcdn.net")

    if platform == "threads":
        return any(
            _host_matches(hostname, domain)
            for domain in ("cdninstagram.com", "fbcdn.net")
        )

    return False


def social_referer(platform: str) -> str:
    return {
        "pinterest": "https://www.pinterest.com/",
        "tiktok": "https://www.tiktok.com/",
        "facebook": "https://www.facebook.com/",
        "threads": "https://www.threads.com/",
    }.get(platform, "https://www.google.com/")


def _positive_int(value: Any) -> int | None:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _normalize_extension(value: Any, media_url: str = "") -> str | None:
    extension = str(value or "").strip().lower().lstrip(".")
    if not extension:
        extension = PurePosixPath(urlparse(media_url).path).suffix.lower().lstrip(".")
    if extension == "jpeg":
        extension = "jpg"
    return extension or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_short_source(source_url: str, platform: str) -> str:
    hostname = _hostname(source_url)
    short_hosts = {"pin.it", "vm.tiktok.com", "vt.tiktok.com", "fb.watch"}
    if hostname not in short_hosts:
        return source_url

    headers = {
        "User-Agent": LINK_PREVIEW_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        response = requests.get(
            source_url,
            headers=headers,
            stream=True,
            timeout=(10, 20),
            allow_redirects=True,
        )
        final_url = response.url
        response.close()
    except requests.RequestException:
        return source_url

    if social_platform(final_url) == platform:
        return final_url.split("#", 1)[0]
    return source_url


def _run_gallery_dl(source_url: str, platform: str) -> list[Any] | None:
    canonical_url = _resolve_short_source(source_url, platform)

    try:
        result = subprocess.run(
            ["gallery-dl", "-j", canonical_url],
            capture_output=True,
            text=True,
            timeout=GALLERY_DL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        return None

    return payload if isinstance(payload, list) else None


def _parse_gallery_dl(source_url: str, platform: str) -> dict[str, Any] | None:
    payload = _run_gallery_dl(source_url, platform)
    if payload is None:
        return None

    post_metadata: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []

    for event in payload:
        if not isinstance(event, list):
            continue

        if len(event) >= 2 and event[0] == 2 and isinstance(event[1], dict):
            post_metadata = event[1]
            continue

        if len(event) < 3 or event[0] != 3:
            continue

        raw_url, metadata = event[1], event[2]
        if not isinstance(raw_url, str) or not isinstance(metadata, dict):
            continue

        media_kind = str(metadata.get("type") or "").strip().lower()
        raw_media_url = raw_url.removeprefix("ytdl:")
        extension = _normalize_extension(metadata.get("extension"), raw_media_url)

        is_image = media_kind in {"photo", "image", "pin"} or \
            extension in IMAGE_EXTENSIONS
        is_video = media_kind in {"video", "animated_gif"} or \
            extension in VIDEO_EXTENSIONS

        # TikTok photo posts also expose their soundtrack. Audio is not a
        # separate visual item and must not appear in the carousel keyboard.
        if not is_image and not is_video:
            continue

        if is_image:
            if extension not in IMAGE_EXTENSIONS:
                continue
            if not is_allowed_social_media_url(platform, raw_media_url, "image"):
                continue
            media_type = "image"
            media_url: str | None = raw_media_url
            thumbnail = raw_media_url
            duration = None
        else:
            media_type = "video"
            # Video selection remains on yt-dlp. gallery-dl is only used to
            # describe mixed posts that contain at least one image.
            media_url = None
            thumbnail = metadata.get("thumbnail")
            duration = _positive_int(metadata.get("duration"))

        index = len(entries) + 1
        item_id = metadata.get("id") or post_metadata.get("id")
        title = _first_text(
            metadata.get("title"),
            metadata.get("description"),
            metadata.get("caption"),
            metadata.get("desc"),
            post_metadata.get("title"),
            post_metadata.get("description"),
            post_metadata.get("caption"),
            post_metadata.get("desc"),
        )

        entries.append(
            {
                "index": index,
                "id": f"{item_id}-{index}" if item_id is not None else str(index),
                "title": title,
                "duration": duration,
                "thumbnail": thumbnail,
                "formats": [],
                "media_type": media_type,
                "media_url": media_url,
                "extension": extension,
                "width": _positive_int(metadata.get("width")),
                "height": _positive_int(metadata.get("height")),
            }
        )

    if not entries:
        return None

    title = _first_text(
        post_metadata.get("title"),
        post_metadata.get("description"),
        post_metadata.get("caption"),
        post_metadata.get("desc"),
        entries[0].get("title"),
        f"{platform.title()} post",
    )
    media_types = {entry["media_type"] for entry in entries}

    return {
        "source_url": source_url,
        "title": title,
        "duration": None,
        "thumbnail": entries[0].get("thumbnail"),
        "formats": [],
        "media_type": "mixed" if len(media_types) > 1 else entries[0]["media_type"],
        "media_url": None,
        "extension": None,
        "width": None,
        "height": None,
        "is_playlist": len(entries) > 1,
        "entry_count": len(entries),
        "entries": entries,
    }


class _ThreadsEmbedParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.video_depth = 0
        self.video_urls: list[str] = []
        self.image_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)

        if tag == "video":
            self.video_depth += 1
            self._add_video(attributes.get("src"))
            return

        if tag == "source" and self.video_depth:
            self._add_video(attributes.get("src"))
            return

        if tag != "img":
            return

        media_url = attributes.get("src")
        if not media_url or not is_allowed_social_media_url("threads", media_url):
            return

        width = _positive_int(attributes.get("width"))
        height = _positive_int(attributes.get("height"))
        parsed_path = urlparse(media_url).path.lower()
        query = urlparse(media_url).query.lower()

        # Exclude the author's small profile picture. Post images in the
        # official embed currently use the *-15 media path and/or explicitly
        # set draggable=false.
        if width and height and max(width, height) <= 200:
            return
        if "s100x100" in query or "t51.82787-19" in parsed_path:
            return

        looks_like_post_media = (
            attributes.get("draggable") == "false"
            or re.search(r"/t51\.[0-9]+-15/", parsed_path) is not None
        )
        if looks_like_post_media:
            self.image_urls.append(media_url)

    def handle_endtag(self, tag: str) -> None:
        if tag == "video" and self.video_depth:
            self.video_depth -= 1

    def _add_video(self, media_url: str | None) -> None:
        if media_url and is_allowed_social_media_url("threads", media_url, "video"):
            self.video_urls.append(media_url)


def _threads_embed_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/embed"):
        embed_path = path
    else:
        embed_path = path + "/embed"
    return f"https://www.threads.com{embed_path}"


def _read_limited_response(response: requests.Response, limit: int) -> str:
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > limit:
            raise RuntimeError("Threads embed response is too large")
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


def _extract_threads_entries(source_url: str) -> list[dict[str, Any]]:
    if not is_threads_url(source_url):
        raise ValueError("Not a Threads URL")

    response = requests.get(
        _threads_embed_url(source_url),
        headers={
            "User-Agent": LINK_PREVIEW_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
        stream=True,
        timeout=THREADS_REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    try:
        response.raise_for_status()
        if not is_threads_url(response.url):
            raise RuntimeError("Threads embed redirected to an unexpected host")
        document = _read_limited_response(response, THREADS_MAX_HTML_BYTES)
    finally:
        response.close()

    parser = _ThreadsEmbedParser()
    parser.feed(document)

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for media_type, urls in (
        ("video", parser.video_urls),
        ("image", parser.image_urls),
    ):
        for media_url in urls:
            parsed = urlparse(media_url)
            identity = ((parsed.hostname or "").lower(), parsed.path)
            if identity in seen:
                continue
            seen.add(identity)
            index = len(entries) + 1
            extension = _normalize_extension(None, media_url)
            if media_type == "image" and extension not in IMAGE_EXTENSIONS:
                extension = "jpg"
            if media_type == "video" and extension not in VIDEO_EXTENSIONS:
                extension = "mp4"
            entries.append(
                {
                    "index": index,
                    "id": str(index),
                    "title": "Threads post",
                    "duration": None,
                    "thumbnail": media_url if media_type == "image" else None,
                    "formats": [],
                    "media_type": media_type,
                    "media_url": media_url,
                    "extension": extension,
                    "width": None,
                    "height": None,
                }
            )

    if not entries:
        raise RuntimeError("No downloadable media found in the public Threads embed")
    return entries


def _threads_quality_from_url(media_url: str) -> int | None:
    try:
        encoded = parse_qs(urlparse(media_url).query).get("efg", [""])[0]
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except Exception:
        return None

    match = re.search(r"\.(\d{2,4})\.(?:dash|baseline)", decoded)
    return _positive_int(match.group(1)) if match else None


def _probe_threads_video(media_url: str) -> dict[str, Any]:
    if not is_allowed_social_media_url("threads", media_url, "video"):
        raise RuntimeError("Unexpected Threads video host")

    command = [
        "ffprobe",
        "-v", "error",
        "-user_agent", BROWSER_USER_AGENT,
        "-headers", f"Referer: {social_referer('threads')}\r\n",
        "-rw_timeout", "15000000",
        "-analyzeduration", "1000000",
        "-probesize", "1000000",
        "-show_entries", "stream=width,height,codec_type",
        "-show_entries", "format=duration,size",
        "-of", "json",
        media_url,
    ]

    payload: dict[str, Any] = {}
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if result.returncode == 0:
            parsed = json.loads(result.stdout)
            if isinstance(parsed, dict):
                payload = parsed
    except (OSError, subprocess.TimeoutExpired, ValueError):
        payload = {}

    video_stream = next(
        (
            stream for stream in payload.get("streams", [])
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        {},
    )
    has_audio = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in payload.get("streams", [])
    )
    width = _positive_int(video_stream.get("width"))
    height = _positive_int(video_stream.get("height"))

    if width is None or height is None:
        quality = _threads_quality_from_url(media_url) or 640
        width = quality
        height = quality

    format_info = payload.get("format")
    if not isinstance(format_info, dict):
        format_info = {}

    duration = _positive_int(format_info.get("duration"))
    filesize = _positive_int(format_info.get("size"))
    return {
        "width": width,
        "height": height,
        "duration": duration,
        "filesize": filesize,
        "has_audio": has_audio,
    }


def _single_entry_info(
    source_url: str,
    title: str | None,
    entry: dict[str, Any],
    *,
    selected_from_playlist: bool,
) -> dict[str, Any]:
    return {
        "source_url": source_url,
        "title": entry.get("title") or title,
        "duration": entry.get("duration"),
        "thumbnail": entry.get("thumbnail"),
        "formats": entry.get("formats") or [],
        "media_type": entry.get("media_type"),
        "media_url": entry.get("media_url"),
        "extension": entry.get("extension"),
        "width": entry.get("width"),
        "height": entry.get("height"),
        "is_playlist": selected_from_playlist,
        "entry_count": 1,
        "entries": [entry],
    }


def _threads_info(source_url: str, playlist_index: int | None) -> dict[str, Any]:
    entries = _extract_threads_entries(source_url)

    if playlist_index is not None:
        selected = next(
            (entry for entry in entries if entry["index"] == playlist_index),
            None,
        )
        if selected is None:
            raise ValueError("Selected Threads media item does not exist")
    elif len(entries) == 1:
        selected = entries[0]
    else:
        selected = None

    if selected is not None:
        if selected["media_type"] == "video":
            probe = _probe_threads_video(selected["media_url"])
            selected = dict(selected)
            selected.update(
                {
                    "duration": probe["duration"],
                    "width": probe["width"],
                    "height": probe["height"],
                    "formats": [
                        {
                            "format_id": "threads-direct",
                            "extension": "mp4",
                            "resolution": f"{probe['width']}x{probe['height']}",
                            "filesize": probe["filesize"],
                            "has_video": True,
                            "has_audio": probe["has_audio"],
                            "video_codec": None,
                            "audio_codec": None,
                        }
                    ],
                }
            )
        return _single_entry_info(
            source_url,
            "Threads post",
            selected,
            selected_from_playlist=playlist_index is not None,
        )

    media_types = {entry["media_type"] for entry in entries}
    return {
        "source_url": source_url,
        "title": "Threads post",
        "duration": None,
        "thumbnail": next(
            (entry["thumbnail"] for entry in entries if entry.get("thumbnail")),
            None,
        ),
        "formats": [],
        "media_type": "mixed" if len(media_types) > 1 else entries[0]["media_type"],
        "media_url": None,
        "extension": None,
        "width": None,
        "height": None,
        "is_playlist": True,
        "entry_count": len(entries),
        "entries": entries,
    }


def get_social_media_info(
    source_url: str,
    playlist_index: int | None = None,
) -> dict[str, Any] | None:
    """Return image/mixed-post metadata, or None to keep the yt-dlp path."""

    platform = social_platform(source_url)
    if platform is None:
        return None
    if platform == "threads":
        return _threads_info(source_url, playlist_index)

    gallery = _parse_gallery_dl(source_url, platform)
    if gallery is None:
        return None

    entries = gallery["entries"]
    if not any(entry["media_type"] == "image" for entry in entries):
        return None

    if playlist_index is not None:
        selected = next(
            (entry for entry in entries if entry["index"] == playlist_index),
            None,
        )
        if selected is None:
            raise ValueError(f"Selected {platform} media item does not exist")
        if selected["media_type"] != "image":
            return None
        return _single_entry_info(
            source_url,
            gallery.get("title"),
            selected,
            selected_from_playlist=True,
        )

    if len(entries) == 1 and entries[0]["media_type"] == "image":
        return _single_entry_info(
            source_url,
            gallery.get("title"),
            entries[0],
            selected_from_playlist=False,
        )
    return gallery


def extract_social_image(
    source_url: str,
    playlist_index: int | None,
) -> dict[str, Any]:
    """Re-extract and select an image immediately before worker download."""

    platform = social_platform(source_url)
    if platform is None:
        raise ValueError("Unsupported social image URL")

    if platform == "threads":
        entries = _extract_threads_entries(source_url)
    else:
        gallery = _parse_gallery_dl(source_url, platform)
        entries = gallery["entries"] if gallery else []

    image_entries = [entry for entry in entries if entry["media_type"] == "image"]
    if playlist_index is not None:
        selected = next(
            (entry for entry in image_entries if entry["index"] == playlist_index),
            None,
        )
    elif len(entries) == 1 and len(image_entries) == 1:
        selected = image_entries[0]
    else:
        selected = None

    if selected is None or not selected.get("media_url"):
        raise RuntimeError("Selected media item is not a downloadable image")

    return {
        "platform": platform,
        "url": selected["media_url"],
        "extension": selected.get("extension") or "jpg",
    }


def extract_threads_video(
    source_url: str,
    playlist_index: int | None,
) -> dict[str, Any]:
    """Resolve one public Threads video URL immediately before download."""

    entries = _extract_threads_entries(source_url)
    videos = [entry for entry in entries if entry["media_type"] == "video"]

    if playlist_index is not None:
        selected = next(
            (entry for entry in videos if entry["index"] == playlist_index),
            None,
        )
    elif len(entries) == 1 and len(videos) == 1:
        selected = videos[0]
    else:
        selected = None

    if selected is None or not selected.get("media_url"):
        raise RuntimeError("Selected Threads media item is not a video")
    if not is_allowed_social_media_url("threads", selected["media_url"], "video"):
        raise RuntimeError("Unexpected Threads video host")

    return {
        "url": selected["media_url"],
        "referer": social_referer("threads"),
    }
