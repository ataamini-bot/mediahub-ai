import json
import subprocess
from datetime import datetime, timezone
from typing import Any
import ipaddress
import os
import re
import socket
from urllib.error import (
    HTTPError,
    URLError,
)
from urllib.parse import urlparse
from urllib.request import (
    Request,
    urlopen,
)

import yt_dlp
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.download_job import (
    DownloadJob,
    DownloadJobStatus,
)
from app.repositories.download_job import (
    DownloadJobRepository,
)
from app.services.download_access import (
    DownloadAccessService,
)
from app.services.social_media import (
    get_social_media_info,
)
from app.workers.tasks.download import (
    cleanup_paused_download,
    download_task,
)


# ============================================================
# Configuration
# ============================================================

BGUTIL_POT_BASE_URL = os.getenv(
    "BGUTIL_POT_BASE_URL",
    "http://bgutil-provider:4416",
)

FILESIZE_PROBE_TIMEOUT = 10

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/136.0 Safari/537.36"
)


# ============================================================
# URL helpers
# ============================================================

def _is_youtube_url(
    source_url: str,
) -> bool:

    value = (
        source_url
        .strip()
        .lower()
    )

    youtube_hosts = (
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
    )

    return any(
        host in value
        for host in youtube_hosts
    )


def _is_safe_probe_url(
    url: str,
) -> bool:

    try:

        parsed = urlparse(
            url
        )

    except Exception:

        return False

    if parsed.scheme not in (
        "http",
        "https",
    ):

        return False

    hostname = (
        parsed.hostname
    )

    if not hostname:

        return False

    if hostname.lower() in (
        "localhost",
        "localhost.localdomain",
    ):

        return False

    # --------------------------------------------------------
    # Literal IP
    # --------------------------------------------------------

    try:

        address = (
            ipaddress.ip_address(
                hostname
            )
        )

        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):

            return False

    except ValueError:

        pass

    # --------------------------------------------------------
    # DNS resolution
    # --------------------------------------------------------

    try:

        resolved = socket.getaddrinfo(
            hostname,
            parsed.port
            or (
                443
                if parsed.scheme == "https"
                else 80
            ),
            type=socket.SOCK_STREAM,
        )

    except OSError:

        return False

    for entry in resolved:

        try:

            resolved_ip = (
                entry[4][0]
            )

            address = (
                ipaddress.ip_address(
                    resolved_ip
                )
            )

        except (
            IndexError,
            ValueError,
        ):

            return False

        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):

            return False

    return True


# ============================================================
# yt-dlp options
# ============================================================

def _get_media_info_options(
    source_url: str,
    playlist_index: int | None = None,
) -> dict[str, Any]:

    options: dict[
        str,
        Any,
    ] = {
        "quiet":
            True,

        "no_warnings":
            True,

        "skip_download":
            True,

        # IMPORTANT:
        # Do not use noplaylist=True here.
        #
        # X posts containing multiple videos are returned
        # by yt-dlp as a playlist.
        "noplaylist":
            False,
    }

    if (
        playlist_index
        is not None
    ):

        options[
            "playlist_items"
        ] = str(
            playlist_index
        )

    if not _is_youtube_url(
        source_url
    ):

        return options

    # --------------------------------------------------------
    # YouTube JS runtime
    # --------------------------------------------------------

    options[
        "js_runtimes"
    ] = {
        "deno": {},
    }

    # --------------------------------------------------------
    # YouTube EJS
    # --------------------------------------------------------

    options[
        "remote_components"
    ] = {
        "ejs:github",
    }

    # --------------------------------------------------------
    # YouTube PO Token
    # --------------------------------------------------------

    options[
        "extractor_args"
    ] = {
        "youtube": {
            "player_client": [
                "mweb",
            ],
        },

        "youtubepot-bgutilhttp": {
            "base_url": [
                BGUTIL_POT_BASE_URL,
            ],
        },
    }

    return options


# ============================================================
# HTTP headers
# ============================================================

def _normalize_http_headers(
    item: dict[str, Any],
) -> dict[str, str]:

    result: dict[
        str,
        str,
    ] = {}

    raw_headers = (
        item.get(
            "http_headers"
        )
        or {}
    )

    if isinstance(
        raw_headers,
        dict,
    ):

        for (
            key,
            value,
        ) in raw_headers.items():

            if (
                key is None
                or value is None
            ):

                continue

            result[
                str(
                    key
                )
            ] = str(
                value
            )

    if not any(
        key.lower()
        == "user-agent"
        for key
        in result
    ):

        result[
            "User-Agent"
        ] = (
            DEFAULT_USER_AGENT
        )

    return result


# ============================================================
# Integer helpers
# ============================================================

def _positive_int(
    value: Any,
) -> int | None:

    if value is None:

        return None

    try:

        result = int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if result <= 0:

        return None

    return result


# ============================================================
# Content-Range
# ============================================================

def _filesize_from_content_range(
    value: str | None,
) -> int | None:

    if not value:

        return None

    value = (
        str(
            value
        )
        .strip()
    )

    if "/" not in value:

        return None

    total = (
        value
        .rsplit(
            "/",
            1,
        )[1]
        .strip()
    )

    if (
        not total
        or total == "*"
    ):

        return None

    return (
        _positive_int(
            total
        )
    )


# ============================================================
# Remote filesize probe
# ============================================================

def _probe_remote_filesize(
    item: dict[str, Any],
) -> int | None:

    stream_url = (
        item.get(
            "url"
        )
    )

    if not isinstance(
        stream_url,
        str,
    ):

        return None

    stream_url = (
        stream_url.strip()
    )

    if not stream_url:

        return None

    if not _is_safe_probe_url(
        stream_url
    ):

        return None

    headers = (
        _normalize_http_headers(
            item
        )
    )

    # ========================================================
    # HEAD
    # ========================================================

    try:

        request = Request(
            stream_url,
            headers=headers,
            method="HEAD",
        )

        with urlopen(
            request,
            timeout=(
                FILESIZE_PROBE_TIMEOUT
            ),
        ) as response:

            range_size = (
                _filesize_from_content_range(
                    response.headers.get(
                        "Content-Range"
                    )
                )
            )

            if (
                range_size
                is not None
            ):

                return range_size

            content_length = (
                _positive_int(
                    response.headers.get(
                        "Content-Length"
                    )
                )
            )

            if (
                content_length
                is not None
            ):

                return (
                    content_length
                )

    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
    ):

        pass

    # ========================================================
    # Range request
    # ========================================================

    range_headers = dict(
        headers
    )

    range_headers[
        "Range"
    ] = (
        "bytes=0-0"
    )

    try:

        request = Request(
            stream_url,
            headers=range_headers,
            method="GET",
        )

        with urlopen(
            request,
            timeout=(
                FILESIZE_PROBE_TIMEOUT
            ),
        ) as response:

            range_size = (
                _filesize_from_content_range(
                    response.headers.get(
                        "Content-Range"
                    )
                )
            )

            if (
                range_size
                is not None
            ):

                return range_size

            status = getattr(
                response,
                "status",
                None,
            )

            # If the server ignored Range and returned the
            # whole resource, Content-Length is trustworthy.
            if status == 200:

                content_length = (
                    _positive_int(
                        response.headers.get(
                            "Content-Length"
                        )
                    )
                )

                if (
                    content_length
                    is not None
                ):

                    return (
                        content_length
                    )

    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
    ):

        pass

    return None


# ============================================================
# Format filesize
# ============================================================

def _is_manifest_format(
    item: dict[str, Any],
) -> bool:

    protocol = str(
        item.get("protocol")
        or ""
    ).strip().lower()

    stream_url = str(
        item.get("url")
        or ""
    ).strip().lower()

    try:
        path = urlparse(stream_url).path.lower()
    except Exception:
        path = ""

    return (
        any(
            marker in protocol
            for marker in (
                "m3u8",
                "dash",
                "ism",
            )
        )
        or path.endswith(".m3u8")
        or path.endswith(".mpd")
        or path.endswith(".ism")
    )


def _estimate_manifest_filesize(
    item: dict[str, Any],
    duration: Any,
) -> int | None:

    try:
        duration_value = float(duration)
    except (TypeError, ValueError):
        return None

    if duration_value <= 0:
        return None

    bitrate = item.get("tbr")

    if bitrate is None:
        try:
            bitrate = float(item.get("vbr") or 0) + float(
                item.get("abr") or 0
            )
        except (TypeError, ValueError):
            bitrate = None

    try:
        bitrate_value = float(bitrate)
    except (TypeError, ValueError):
        return None

    if bitrate_value <= 0:
        return None

    # yt-dlp bitrates are Kbit/s. Include a small allowance for segment and
    # container overhead; the bot labels all pre-download sizes approximate.
    return int(
        bitrate_value
        * 1000
        / 8
        * duration_value
        * 1.03
    )


def _should_probe_direct_filesize(
    stream_url: str,
) -> bool:

    try:
        hostname = (
            urlparse(stream_url).hostname
            or ""
        ).lower()
    except Exception:
        return False

    exact_size_domains = (
        "twimg.com",
        "pinimg.com",
        "dmcdn.net",
        "vimeocdn.com",
        "fbcdn.net",
        "cdninstagram.com",
    )

    return any(
        hostname == domain
        or hostname.endswith("." + domain)
        for domain in exact_size_domains
    ) or bool(
        re.fullmatch(
            r"(?:[a-z0-9-]+\.)*[a-z0-9-]*tiktokcdn"
            r"(?:-[a-z0-9-]+)?\.com",
            hostname,
        )
    )


def _get_format_filesize(
    item: dict[str, Any],
    duration: Any = None,
) -> int | None:

    # Never report the byte size of an HLS/DASH manifest as the size of the
    # actual video. Prefer extractor estimates, then calculate from bitrate.
    if _is_manifest_format(item):

        filesize_approx = _positive_int(
            item.get("filesize_approx")
        )

        if filesize_approx is not None:
            return filesize_approx

        bitrate_estimate = _estimate_manifest_filesize(
            item,
            duration,
        )

        if bitrate_estimate is not None:
            return bitrate_estimate

        extractor_size = _positive_int(
            item.get("filesize")
        )

        # A tiny value here is the playlist document itself, not the media.
        if extractor_size is not None and extractor_size > 4096:
            return extractor_size

        return None

    # Exact extractor value always wins.
    filesize = (
        _positive_int(
            item.get(
                "filesize"
            )
        )
    )

    if filesize is not None:

        return filesize

    stream_url = (
        str(
            item.get(
                "url"
            )
            or ""
        )
        .strip()
        .lower()
    )

    # Known media CDNs expose an exact Content-Length / Content-Range. Probe
    # them before falling back to an extractor approximation.
    if _should_probe_direct_filesize(stream_url):

        probed_size = (
            _probe_remote_filesize(
                item
            )
        )

        if probed_size is not None:

            return probed_size

    filesize_approx = (
        _positive_int(
            item.get(
                "filesize_approx"
            )
        )
    )

    if (
        filesize_approx
        is not None
    ):

        return filesize_approx

    return (
        _probe_remote_filesize(
            item
        )
    )


# ============================================================
# Format detection
# ============================================================

def _detect_stream_types(
    item: dict[str, Any],
) -> tuple[
    bool,
    bool,
]:

    vcodec = (
        item.get(
            "vcodec"
        )
    )

    acodec = (
        item.get(
            "acodec"
        )
    )

    width = (
        _positive_int(
            item.get(
                "width"
            )
        )
    )

    height = (
        _positive_int(
            item.get(
                "height"
            )
        )
    )

    format_id = (
        str(
            item.get(
                "format_id"
            )
            or ""
        )
        .lower()
    )

    resolution = (
        str(
            item.get(
                "resolution"
            )
            or ""
        )
        .lower()
    )

    # --------------------------------------------------------
    # Video
    #
    # X/Twitter HTTP formats sometimes return:
    #
    # vcodec=None
    # acodec=None
    # width=364
    # height=640
    #
    # Dimensions are enough to classify these as video.
    # --------------------------------------------------------

    has_video = (
        (
            vcodec
            not in (
                None,
                "none",
                "images",
            )
        )
        or (
            width is not None
            and height is not None
        )
    )

    # --------------------------------------------------------
    # Audio
    #
    # X HLS audio formats can have acodec=None but their
    # format_id clearly identifies them as audio.
    # --------------------------------------------------------

    has_audio = (
        acodec
        not in (
            None,
            "none",
        )
    )

    if (
        not has_audio
        and not has_video
        and (
            "audio"
            in format_id
            or resolution
            == "audio only"
        )
    ):

        has_audio = True

    return (
        has_video,
        has_audio,
    )


# ============================================================
# Normalize one format
# ============================================================

def _normalize_format(
    item: dict[str, Any],
    duration: Any = None,
) -> dict[str, Any] | None:

    format_id = (
        item.get(
            "format_id"
        )
    )

    if not format_id:

        return None

    if (
        item.get(
            "vcodec"
        )
        == "images"
        or item.get(
            "protocol"
        )
        == "mhtml"
    ):

        return None

    (
        has_video,
        has_audio,
    ) = (
        _detect_stream_types(
            item
        )
    )

    width = (
        _positive_int(
            item.get(
                "width"
            )
        )
    )

    height = (
        _positive_int(
            item.get(
                "height"
            )
        )
    )

    resolution = (
        item.get(
            "resolution"
        )
    )

    if (
        width is not None
        and height is not None
    ):

        resolution = (
            f"{width}x{height}"
        )

    elif (
        not has_video
        and has_audio
    ):

        resolution = (
            "audio only"
        )

    extension = (
        item.get(
            "ext"
        )
    )

    if (
        extension
        is not None
    ):

        extension = (
            str(
                extension
            )
        )

    vcodec = (
        item.get(
            "vcodec"
        )
    )

    acodec = (
        item.get(
            "acodec"
        )
    )

    video_codec: (
        str
        | None
    ) = None

    audio_codec: (
        str
        | None
    ) = None

    if (
        has_video
        and vcodec
        not in (
            None,
            "none",
        )
    ):

        video_codec = (
            str(
                vcodec
            )
        )

    if (
        has_audio
        and acodec
        not in (
            None,
            "none",
        )
    ):

        audio_codec = (
            str(
                acodec
            )
        )

    filesize = (
        _get_format_filesize(
            item,
            duration,
        )
    )

    return {
        "format_id":
            str(
                format_id
            ),

        "extension":
            extension,

        "resolution":
            resolution,

        "filesize":
            filesize,

        "has_video":
            has_video,

        "has_audio":
            has_audio,

        "video_codec":
            video_codec,

        "audio_codec":
            audio_codec,
    }


# ============================================================
# Normalize format list
# ============================================================

def _normalize_formats(
    raw_formats: list[Any] | None,
    duration: Any = None,
) -> list[
    dict[str, Any]
]:

    result: list[
        dict[str, Any]
    ] = []

    for item in (
        raw_formats
        or []
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        normalized = (
            _normalize_format(
                item,
                duration,
            )
        )

        if (
            normalized
            is None
        ):

            continue

        result.append(
            normalized
        )

    return result


# ============================================================
# Normalize duration
# ============================================================

def _normalize_duration(
    value: Any,
) -> int | None:

    if value is None:

        return None

    try:

        duration = int(
            float(
                value
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if duration <= 0:

        return None

    return duration


# ============================================================
# Download Service
# ============================================================


# ============================================================
# Instagram mixed-media helpers
# ============================================================



def _is_instagram_story_url(
    source_url: str,
) -> bool:

    if not _is_instagram_url(
        source_url
    ):

        return False

    try:

        path = (
            urlparse(
                source_url
            ).path
            or ""
        ).lower()

    except Exception:

        return False

    return path.startswith(
        "/stories/"
    )


INSTAGRAM_SHORTCODE_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)


def _get_instagram_story_media_id(
    source_url: str,
) -> int | None:

    if not _is_instagram_story_url(
        source_url
    ):

        return None

    try:

        parts = [
            part
            for part in (
                urlparse(
                    source_url
                ).path
                or ""
            ).split("/")
            if part
        ]

    except Exception:

        return None

    if (
        len(parts) < 3
        or parts[0].lower()
        != "stories"
    ):

        return None

    story_id = (
        parts[2]
        .strip()
    )

    if not story_id.isdigit():

        return None

    return int(
        story_id
    )


def _decode_instagram_shortcode_media_id(
    shortcode: str | None,
) -> int | None:

    value = str(
        shortcode
        or ""
    ).strip()

    if not value:

        return None

    result = 0

    for character in value:

        position = (
            INSTAGRAM_SHORTCODE_ALPHABET.find(
                character
            )
        )

        if position < 0:

            return None

        result = (
            result * 64
            + position
        )

    return result







def _is_instagram_url(
    source_url: str,
) -> bool:

    try:

        hostname = (
            urlparse(
                source_url
            ).hostname
            or ""
        ).lower()

    except Exception:

        return False

    return (
        hostname == "instagram.com"
        or hostname.endswith(
            ".instagram.com"
        )
    )


def _get_instagram_gallery_info(
    source_url: str,
) -> dict[str, Any] | None:

    if not _is_instagram_url(
        source_url
    ):

        return None

    if _is_instagram_story_url(
        source_url
    ):

        return None

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "socket_timeout": 15,
        "ignore_no_formats_error": True,
    }

    try:

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            raw_info = (
                ydl.extract_info(
                    source_url,
                    download=False,
                    process=False,
                )
            )

    except Exception as exc:

        print(
            "Instagram raw yt-dlp extraction failed: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return None

    if not isinstance(
        raw_info,
        dict,
    ):

        return None

    if (
        raw_info.get(
            "_type"
        )
        == "playlist"
    ):

        raw_entries = (
            raw_info.get(
                "entries"
            )
            or []
        )

    else:

        raw_entries = [
            raw_info
        ]

    entries: list[
        dict[str, Any]
    ] = []

    for index, item in enumerate(
        raw_entries,
        start=1,
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        formats = (
            item.get(
                "formats"
            )
            or []
        )

        thumbnails = [
            thumbnail
            for thumbnail in (
                item.get(
                    "thumbnails"
                )
                or []
            )
            if isinstance(
                thumbnail,
                dict,
            )
            and isinstance(
                thumbnail.get(
                    "url"
                ),
                str,
            )
        ]

        def thumbnail_score(
            thumbnail: dict[str, Any],
        ) -> tuple[
            int,
            int,
            int,
        ]:

            try:
                width = int(
                    thumbnail.get(
                        "width"
                    )
                    or 0
                )
            except (
                TypeError,
                ValueError,
            ):
                width = 0

            try:
                height = int(
                    thumbnail.get(
                        "height"
                    )
                    or 0
                )
            except (
                TypeError,
                ValueError,
            ):
                height = 0

            return (
                width * height,
                width,
                height,
            )

        # yt-dlp normally orders Instagram thumbnails from
        # smaller to larger variants.
        #
        # Some image-only posts do not include width/height
        # metadata at all. In that case every thumbnail receives
        # the same resolution score, so use the original list
        # position as the final tie-breaker and prefer the last
        # (highest-quality) candidate.
        best_thumbnail = (
            max(
                enumerate(
                    thumbnails
                ),
                key=lambda pair: (
                    *thumbnail_score(
                        pair[1]
                    ),
                    pair[0],
                ),
            )[1]
            if thumbnails
            else {}
        )

        thumbnail_url = (
            item.get(
                "thumbnail"
            )
            or best_thumbnail.get(
                "url"
            )
        )

        if formats:

            media_type = "video"
            media_url = None

            extension = (
                item.get(
                    "ext"
                )
                or "mp4"
            )

        else:

            media_type = "image"

            media_url = (
                best_thumbnail.get(
                    "url"
                )
                or item.get(
                    "thumbnail"
                )
            )

            if not media_url:

                continue

            extension = (
                best_thumbnail.get(
                    "ext"
                )
                or item.get(
                    "ext"
                )
                or "jpg"
            )

        entries.append(
            {
                "index":
                    index,

                "id":
                    (
                        str(
                            item.get(
                                "id"
                            )
                        )
                        if item.get(
                            "id"
                        )
                        is not None
                        else None
                    ),

                "title":
                    item.get(
                        "title"
                    ),

                "duration":
                    item.get(
                        "duration"
                    ),

                "thumbnail":
                    thumbnail_url,

                # Video formats are intentionally resolved
                # again by the normal yt-dlp path when the
                # user selects the specific carousel item.
                "formats":
                    [],

                "media_type":
                    media_type,

                "media_url":
                    media_url,

                "extension":
                    str(
                        extension
                    ).lower(),

                "width":
                    (
                        best_thumbnail.get(
                            "width"
                        )
                        or item.get(
                            "width"
                        )
                    ),

                "height":
                    (
                        best_thumbnail.get(
                            "height"
                        )
                        or item.get(
                            "height"
                        )
                    ),
            }
        )

    if not entries:

        return None

    media_types = {
        item[
            "media_type"
        ]
        for item in entries
    }

    return {
        "source_url":
            source_url,

        "title":
            raw_info.get(
                "title"
            ),

        "duration":
            None,

        "thumbnail":
            entries[0].get(
                "thumbnail"
            ),

        "formats":
            [],

        "media_type":
            (
                "mixed"
                if len(
                    media_types
                )
                > 1
                else entries[0][
                    "media_type"
                ]
            ),

        "media_url":
            None,

        "extension":
            None,

        "width":
            None,

        "height":
            None,

        "is_playlist":
            len(
                entries
            )
            > 1,

        "entry_count":
            len(
                entries
            ),

        "entries":
            entries,
    }


def _is_x_url(
    source_url: str,
) -> bool:

    try:

        hostname = (
            urlparse(
                source_url
            ).hostname
            or ""
        ).lower()

    except Exception:

        return False

    return hostname in {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }


def _is_allowed_x_image_url(
    media_url: str,
) -> bool:

    try:

        parsed = urlparse(
            media_url
        )

        hostname = (
            parsed.hostname
            or ""
        ).lower()

    except Exception:

        return False

    return (
        parsed.scheme == "https"
        and hostname == "pbs.twimg.com"
        and parsed.path.startswith(
            "/media/"
        )
    )


def _get_x_gallery_info(
    source_url: str,
) -> dict[str, Any] | None:

    if not _is_x_url(
        source_url
    ):

        return None

    result = subprocess.run(
        [
            "gallery-dl",
            "-j",
            source_url,
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    # Keep the existing yt-dlp path available whenever
    # gallery-dl cannot resolve the tweet.
    if result.returncode != 0:

        return None

    try:

        payload = json.loads(
            result.stdout
        )

    except Exception:

        return None

    if not isinstance(
        payload,
        list,
    ):

        return None

    post_metadata: dict[
        str,
        Any,
    ] = {}

    entries: list[
        dict[str, Any]
    ] = []

    for event in payload:

        if not isinstance(
            event,
            list,
        ):

            continue

        # ----------------------------------------------------
        # Tweet metadata
        # ----------------------------------------------------

        if (
            len(event) >= 2
            and event[0] == 2
            and isinstance(
                event[1],
                dict,
            )
        ):

            post_metadata = (
                event[1]
            )

            continue

        # ----------------------------------------------------
        # Media item
        # ----------------------------------------------------

        if (
            len(event) < 3
            or event[0] != 3
        ):

            continue

        raw_url = (
            event[1]
        )

        metadata = (
            event[2]
        )

        if (
            not isinstance(
                raw_url,
                str,
            )
            or not isinstance(
                metadata,
                dict,
            )
        ):

            continue

        media_kind = (
            str(
                metadata.get(
                    "type"
                )
                or ""
            )
            .strip()
            .lower()
        )

        extension = (
            str(
                metadata.get(
                    "extension"
                )
                or ""
            )
            .strip()
            .lower()
            .lstrip(".")
        )

        if extension == "jpeg":

            extension = "jpg"

        try:

            index = int(
                metadata.get(
                    "num",
                    len(entries) + 1,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        try:

            width = int(
                metadata.get(
                    "width"
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            width = None

        try:

            height = int(
                metadata.get(
                    "height"
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            height = None

        # ----------------------------------------------------
        # Detect image
        # ----------------------------------------------------

        is_image = (
            media_kind
            in {
                "photo",
                "image",
            }
            or extension
            in {
                "jpg",
                "png",
                "webp",
                "gif",
                "avif",
            }
        )

        # ----------------------------------------------------
        # Detect video
        # ----------------------------------------------------

        is_video = (
            media_kind
            in {
                "video",
                "animated_gif",
            }
            or extension
            in {
                "mp4",
                "webm",
                "m3u8",
            }
        )

        if (
            not is_image
            and not is_video
        ):

            continue

        # ----------------------------------------------------
        # Image
        # ----------------------------------------------------

        if is_image:

            if extension not in {
                "jpg",
                "png",
                "webp",
                "gif",
                "avif",
            }:

                continue

            if not _is_allowed_x_image_url(
                raw_url
            ):

                continue

            media_type = (
                "image"
            )

            media_url = (
                raw_url
            )

            thumbnail = (
                raw_url
            )

            duration = None

        # ----------------------------------------------------
        # Video
        #
        # Do NOT use the video.twimg.com URL directly.
        # Worker continues to download videos through yt-dlp.
        # ----------------------------------------------------

        else:

            media_type = (
                "video"
            )

            media_url = None

            thumbnail = (
                metadata.get(
                    "thumbnail"
                )
            )

            try:

                duration_value = (
                    metadata.get(
                        "duration"
                    )
                )

                duration = (
                    int(
                        float(
                            duration_value
                        )
                    )
                    if duration_value
                    else None
                )

            except (
                TypeError,
                ValueError,
            ):

                duration = None

        tweet_id = (
            metadata.get(
                "tweet_id"
            )
            or post_metadata.get(
                "tweet_id"
            )
        )

        content = (
            metadata.get(
                "content"
            )
            or post_metadata.get(
                "content"
            )
        )

        entries.append(
            {
                "index":
                    index,

                "id":
                    (
                        f"{tweet_id}-{index}"
                        if tweet_id is not None
                        else str(index)
                    ),

                "title":
                    (
                        str(content)
                        if content
                        else None
                    ),

                "duration":
                    duration,

                "thumbnail":
                    thumbnail,

                "formats":
                    [],

                "media_type":
                    media_type,

                "media_url":
                    media_url,

                "extension":
                    extension,

                "width":
                    width,

                "height":
                    height,
            }
        )

    if not entries:

        return None

    entries.sort(
        key=lambda item: (
            int(
                item.get(
                    "index",
                    0,
                )
            )
        )
    )

    media_types = {
        str(
            entry.get(
                "media_type"
            )
        )
        for entry in entries
    }

    # ========================================================
    # Video-only X post
    #
    # Preserve the existing yt-dlp multi-video implementation.
    # ========================================================

    if media_types == {
        "video"
    }:

        return None

    # ========================================================
    # Title
    # ========================================================

    title = (
        post_metadata.get(
            "content"
        )
    )

    if not title:

        author = (
            post_metadata.get(
                "author"
            )
            or post_metadata.get(
                "user"
            )
        )

        if isinstance(
            author,
            dict,
        ):

            author_name = (
                author.get(
                    "name"
                )
                or author.get(
                    "nick"
                )
                or author.get(
                    "username"
                )
            )

        else:

            author_name = (
                author
            )

        if author_name:

            title = (
                f"Post by {author_name}"
            )

        else:

            title = "X post"

    # ========================================================
    # Single image
    # ========================================================

    if (
        len(entries) == 1
        and entries[0].get(
            "media_type"
        )
        == "image"
    ):

        selected = (
            entries[0]
        )

        return {
            "source_url":
                source_url,

            "title":
                title,

            "duration":
                None,

            "thumbnail":
                selected.get(
                    "thumbnail"
                ),

            "formats":
                [],

            "media_type":
                "image",

            "media_url":
                selected.get(
                    "media_url"
                ),

            "extension":
                selected.get(
                    "extension"
                ),

            "width":
                selected.get(
                    "width"
                ),

            "height":
                selected.get(
                    "height"
                ),

            "is_playlist":
                False,

            "entry_count":
                0,

            "entries":
                [],
        }

    # ========================================================
    # Multi-image / mixed media
    # ========================================================

    top_media_type = (
        "mixed"
        if len(
            media_types
        ) > 1
        else next(
            iter(
                media_types
            )
        )
    )

    first_thumbnail = next(
        (
            entry.get(
                "thumbnail"
            )
            for entry
            in entries
            if entry.get(
                "thumbnail"
            )
        ),
        None,
    )

    return {
        "source_url":
            source_url,

        "title":
            title,

        "duration":
            None,

        "thumbnail":
            first_thumbnail,

        "formats":
            [],

        "media_type":
            top_media_type,

        "media_url":
            None,

        "extension":
            None,

        "width":
            None,

        "height":
            None,

        "is_playlist":
            True,

        "entry_count":
            len(
                entries
            ),

        "entries":
            entries,
    }



class DownloadService:

    def __init__(
        self,
        session: AsyncSession,
    ):

        self.repository = (
            DownloadJobRepository(
                session
            )
        )

        self.session = (
            session
        )

    # ========================================================
    # Create Job
    # ========================================================

    async def create_job(
        self,
        source_url: str,
        telegram_id: int,
        format_id: str | None = None,
        quality: str | None = None,
        media_type: str | None = None,
        playlist_index: int | None = None,
        estimated_size_bytes: int | None = None,
    ) -> DownloadJob:

        entitlement = await DownloadAccessService(
            self.session
        ).authorize_job(
            telegram_id=telegram_id,
            quality=quality,
            estimated_size_bytes=estimated_size_bytes,
        )

        job = (
            await self.repository.create(
                source_url=(
                    source_url
                ),
                user_id=entitlement.user_id,
                plan_id=entitlement.plan_id,
                plan_name_snapshot=entitlement.plan_name,
                plan_limits_snapshot=entitlement.limits_snapshot(),
                format_id=(
                    format_id
                ),
                quality=(
                    quality
                ),
                media_type=(
                    media_type
                ),
                playlist_index=playlist_index,
            )
        )

        # ----------------------------------------------------
        # Queue Celery task
        # ----------------------------------------------------

        try:
            task = download_task.apply_async(
                args=[job.id],
                priority=(
                    0
                    if entitlement.priority_processing
                    else 5
                ),
            )
        except Exception as exc:
            job.status = DownloadJobStatus.FAILED
            job.error_message = "Unable to queue download task"
            await self.session.commit()
            raise RuntimeError("Unable to queue download task") from exc

        # ----------------------------------------------------
        # Save Celery task ID
        # ----------------------------------------------------

        job.celery_task_id = (
            task.id
        )

        await (
            self.session.commit()
        )

        await (
            self.session.refresh(
                job
            )
        )

        return job

    # ========================================================
    # Get Job
    # ========================================================

    async def get_job(
        self,
        job_id: int,
    ) -> DownloadJob | None:

        return (
            await self.repository.get_by_id(
                job_id
            )
        )


    # ========================================================
    # Pause Job
    # ========================================================

    async def pause_job(
        self,
        job_id: int,
    ) -> DownloadJob:

        job = (
            await self.repository.get_by_id(
                job_id
            )
        )

        if job is None:

            raise LookupError(
                "Download job not found"
            )

        if job.status not in (
            DownloadJobStatus.PENDING,
            DownloadJobStatus.PROCESSING,
        ):

            raise ValueError(
                "Only pending or processing "
                "downloads can be paused"
            )

        job.status = (
            DownloadJobStatus.PAUSED
        )

        job.paused_at = (
            datetime.now(
                timezone.utc
            )
        )

        job.speed = None
        job.eta = None

        await (
            self.session.commit()
        )

        await (
            self.session.refresh(
                job
            )
        )

        # ----------------------------------------------------
        # Stop the currently running Celery task.
        #
        # The .part file is intentionally kept so yt-dlp
        # can continue it on resume.
        # ----------------------------------------------------

        if job.celery_task_id:

            try:

                download_task.app.control.revoke(
                    job.celery_task_id,
                    terminate=True,
                    signal="SIGTERM",
                )

            except Exception as exc:

                print(
                    "Failed to revoke paused task "
                    f"{job.celery_task_id}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

        # ----------------------------------------------------
        # Cleanup paused partial files after 6 hours.
        # The cleanup task itself verifies that the job is
        # still paused before deleting anything.
        # ----------------------------------------------------

        try:

            cleanup_paused_download.apply_async(
                args=[
                    job.id,
                ],
                countdown=(
                    6
                    * 60
                    * 60
                ),
            )

        except Exception as exc:

            print(
                "Failed to schedule paused cleanup: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        return job

    # ========================================================
    # Resume Job
    # ========================================================

    async def resume_job(
        self,
        job_id: int,
    ) -> DownloadJob:

        job = (
            await self.repository.get_by_id(
                job_id
            )
        )

        if job is None:

            raise LookupError(
                "Download job not found"
            )

        if (
            job.status
            != DownloadJobStatus.PAUSED
        ):

            raise ValueError(
                "Only paused downloads "
                "can be resumed"
            )

        priority_processing = await DownloadAccessService(
            self.session
        ).authorize_resume(job)

        job.status = (
            DownloadJobStatus.PENDING
        )

        job.paused_at = None
        job.expired_at = None
        job.error_message = None

        job.speed = None
        job.eta = None

        await (
            self.session.commit()
        )

        await (
            self.session.refresh(
                job
            )
        )

        try:
            task = download_task.apply_async(
                args=[job.id],
                priority=(
                    0
                    if priority_processing
                    else 5
                ),
            )
        except Exception as exc:
            job.status = DownloadJobStatus.PAUSED
            job.paused_at = datetime.now(timezone.utc)
            job.error_message = "Unable to queue resumed download task"
            await self.session.commit()
            raise RuntimeError("Unable to queue resumed download task") from exc

        job.celery_task_id = (
            task.id
        )

        await (
            self.session.commit()
        )

        await (
            self.session.refresh(
                job
            )
        )

        return job

    # ========================================================
    # Cancel Job
    # ========================================================

    async def cancel_job(
        self,
        job_id: int,
    ) -> DownloadJob:

        job = (
            await self.repository.get_by_id(
                job_id
            )
        )

        if job is None:

            raise LookupError(
                "Download job not found"
            )

        if job.status in (
            DownloadJobStatus.COMPLETED,
            DownloadJobStatus.CANCELLED,
            DownloadJobStatus.EXPIRED,
        ):

            raise ValueError(
                "This download cannot be cancelled"
            )

        old_task_id = (
            job.celery_task_id
        )

        job.status = (
            DownloadJobStatus.CANCELLED
        )

        job.cancelled_at = (
            datetime.now(
                timezone.utc
            )
        )

        job.speed = None
        job.eta = None

        await (
            self.session.commit()
        )

        await (
            self.session.refresh(
                job
            )
        )

        if old_task_id:

            try:

                download_task.app.control.revoke(
                    old_task_id,
                    terminate=True,
                    signal="SIGTERM",
                )

            except Exception as exc:

                print(
                    "Failed to revoke cancelled task "
                    f"{old_task_id}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

        return job

    # ========================================================
    # Get Media Info
    # ========================================================

    @staticmethod
    def get_media_info(
        source_url: str,
        playlist_index: int | None = None,
    ) -> dict[str, Any]:

        if _is_instagram_story_url(
            source_url
        ):

            raise ValueError(
                "Instagram Stories and Highlights "
                "are unavailable in public-only mode"
            )

        # ====================================================
        # Public image posts + Threads
        #
        # Pinterest, TikTok and Facebook image posts are not
        # represented as downloadable video formats by yt-dlp.
        # Threads currently has no yt-dlp extractor at all.
        # Video-only posts on the other platforms return None
        # here and continue through the normal yt-dlp path.
        # ====================================================

        social_media_info = get_social_media_info(
            source_url,
            playlist_index,
        )

        if social_media_info is not None:
            return social_media_info

        # ====================================================
        # Instagram mixed media
        # ====================================================

        instagram_gallery = None

        if _is_instagram_url(
            source_url
        ):

            instagram_gallery = (
                _get_instagram_gallery_info(
                    source_url
                )
            )

        if instagram_gallery:

            gallery_entries = (
                instagram_gallery.get(
                    "entries"
                )
                or []
            )

            # ------------------------------------------------
            # Selected image
            #
            # Images do not go through yt-dlp because
            # yt-dlp treats Instagram photos as
            # "No video formats found".
            # ------------------------------------------------

            if (
                playlist_index
                is not None
            ):

                selected_entry = next(
                    (
                        item
                        for item
                        in gallery_entries
                        if (
                            item.get(
                                "index"
                            )
                            == playlist_index
                        )
                    ),
                    None,
                )

                if (
                    selected_entry
                    and selected_entry.get(
                        "media_type"
                    )
                    == "image"
                ):

                    return {
                        "source_url":
                            source_url,

                        "title":
                            instagram_gallery.get(
                                "title"
                            ),

                        "duration":
                            None,

                        "thumbnail":
                            selected_entry.get(
                                "thumbnail"
                            ),

                        "formats":
                            [],

                        "media_type":
                            "image",

                        "media_url":
                            selected_entry.get(
                                "media_url"
                            ),

                        "extension":
                            selected_entry.get(
                                "extension"
                            ),

                        "width":
                            selected_entry.get(
                                "width"
                            ),

                        "height":
                            selected_entry.get(
                                "height"
                            ),

                        "is_playlist":
                            True,

                        "entry_count":
                            1,

                        "entries":
                            [
                                selected_entry
                            ],
                    }

            # ------------------------------------------------
            # Multi-media/carousel.
            #
            # Return gallery structure before yt-dlp attempts
            # to parse photo entries.
            # ------------------------------------------------

            if (
                playlist_index
                is None
                and len(
                    gallery_entries
                )
                > 1
            ):

                return (
                    instagram_gallery
                )

            # ------------------------------------------------
            # Single Instagram image
            # ------------------------------------------------

            if (
                playlist_index
                is None
                and len(
                    gallery_entries
                )
                == 1
                and gallery_entries[
                    0
                ].get(
                    "media_type"
                )
                == "image"
            ):

                selected_entry = (
                    gallery_entries[0]
                )

                return {
                    **instagram_gallery,

                    "media_type":
                        "image",

                    "media_url":
                        selected_entry.get(
                            "media_url"
                        ),

                    "extension":
                        selected_entry.get(
                            "extension"
                        ),

                    "width":
                        selected_entry.get(
                            "width"
                        ),

                    "height":
                        selected_entry.get(
                            "height"
                        ),

                    "is_playlist":
                        False,

                    "entry_count":
                        1,

                    "entries":
                        [
                            selected_entry
                        ],
                }

        # ====================================================
        # X / Twitter image + mixed-media extraction
        # ====================================================

        if _is_x_url(
            source_url
        ):

            x_gallery_info = (
                _get_x_gallery_info(
                    source_url
                )
            )

            if (
                x_gallery_info
                is not None
            ):

                entries = (
                    x_gallery_info.get(
                        "entries"
                    )
                    or []
                )

                # --------------------------------------------
                # Full post request
                # --------------------------------------------

                if (
                    playlist_index
                    is None
                ):

                    return (
                        x_gallery_info
                    )

                # --------------------------------------------
                # Multi-image / mixed-media item
                # --------------------------------------------

                if entries:

                    selected = next(
                        (
                            entry
                            for entry
                            in entries
                            if int(
                                entry.get(
                                    "index",
                                    -1,
                                )
                            )
                            == playlist_index
                        ),
                        None,
                    )

                    if selected is None:

                        raise ValueError(
                            "Selected X media item "
                            "does not exist"
                        )

                    selected_type = (
                        str(
                            selected.get(
                                "media_type"
                            )
                            or ""
                        )
                        .strip()
                        .lower()
                    )

                    # ----------------------------------------
                    # Image:
                    # Return gallery-dl metadata directly.
                    # ----------------------------------------

                    if (
                        selected_type
                        == "image"
                    ):

                        return {
                            "source_url":
                                source_url,

                            "title":
                                (
                                    selected.get(
                                        "title"
                                    )
                                    or x_gallery_info.get(
                                        "title"
                                    )
                                ),

                            "duration":
                                None,

                            "thumbnail":
                                selected.get(
                                    "thumbnail"
                                ),

                            "formats":
                                [],

                            "media_type":
                                "image",

                            "media_url":
                                selected.get(
                                    "media_url"
                                ),

                            "extension":
                                selected.get(
                                    "extension"
                                ),

                            "width":
                                selected.get(
                                    "width"
                                ),

                            "height":
                                selected.get(
                                    "height"
                                ),

                            "is_playlist":
                                True,

                            "entry_count":
                                1,

                            "entries":
                                [
                                    selected,
                                ],
                        }

                    # ----------------------------------------
                    # Video:
                    #
                    # Do not return gallery-dl's direct URL.
                    # Fall through to the existing yt-dlp
                    # extraction below so formats, qualities
                    # and filesize calculations remain intact.
                    # ----------------------------------------

                    if (
                        selected_type
                        != "video"
                    ):

                        raise ValueError(
                            "Unsupported X media type"
                        )

                # --------------------------------------------
                # Single image with explicit index=1
                # --------------------------------------------

                elif (
                    str(
                        x_gallery_info.get(
                            "media_type"
                        )
                        or ""
                    )
                    .lower()
                    == "image"
                ):

                    if (
                        playlist_index
                        != 1
                    ):

                        raise ValueError(
                            "Selected X media item "
                            "does not exist"
                        )

                    return (
                        x_gallery_info
                    )

        options = (
            _get_media_info_options(
                source_url=source_url,
                playlist_index=(
                    playlist_index
                ),
            )
        )

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            info = (
                ydl.extract_info(
                    source_url,
                    download=False,
                )
            )

        # ====================================================
        # Playlist / multi-video
        # ====================================================

        if (
            info.get(
                "_type"
            )
            == "playlist"
        ):

            raw_entries = (
                info.get(
                    "entries"
                )
                or []
            )

            story_playlist_index: (
                int
                | None
            ) = None

            # ------------------------------------------------
            # Instagram Story URLs point to one exact Story,
            # but yt-dlp may return all currently active
            # Stories from that account.
            #
            # Match the numeric Story media ID from the URL
            # against each entry's Instagram shortcode.
            # ------------------------------------------------

            if (
                playlist_index
                is None
                and _is_instagram_story_url(
                    source_url
                )
            ):

                target_story_id = (
                    _get_instagram_story_media_id(
                        source_url
                    )
                )

                if target_story_id is not None:

                    matched_story = None

                    for (
                        candidate_index,
                        candidate_entry,
                    ) in enumerate(
                        raw_entries,
                        start=1,
                    ):

                        if not isinstance(
                            candidate_entry,
                            dict,
                        ):

                            continue

                        candidate_shortcode = (
                            candidate_entry.get(
                                "id"
                            )
                            or candidate_entry.get(
                                "display_id"
                            )
                        )

                        candidate_story_id = (
                            _decode_instagram_shortcode_media_id(
                                (
                                    str(
                                        candidate_shortcode
                                    )
                                    if candidate_shortcode
                                    is not None
                                    else None
                                )
                            )
                        )

                        if (
                            candidate_story_id
                            == target_story_id
                        ):

                            matched_story = (
                                candidate_index,
                                candidate_entry,
                            )

                            break

                    if matched_story is None:

                        raise ValueError(
                            "Requested Instagram Story "
                            "was not found"
                        )

                    (
                        story_playlist_index,
                        matched_story_entry,
                    ) = matched_story

                    raw_entries = [
                        matched_story_entry
                    ]

            entries: list[
                dict[str, Any]
            ] = []

            for (
                index,
                raw_entry,
            ) in enumerate(
                raw_entries,
                start=1,
            ):

                if not isinstance(
                    raw_entry,
                    dict,
                ):

                    continue

                entry_duration = (
                    _normalize_duration(
                        raw_entry.get(
                            "duration"
                        )
                    )
                )

                entry_formats = (
                    _normalize_formats(
                        raw_entry.get(
                            "formats"
                        ),
                        duration=(
                            entry_duration
                        ),
                    )
                )

                entries.append(
                    {
                        "index":
                            (
                                playlist_index
                                if (
                                    playlist_index
                                    is not None
                                )
                                else (
                                    story_playlist_index
                                    if (
                                        story_playlist_index
                                        is not None
                                    )
                                    else index
                                )
                            ),

                        "id":
                            (
                                str(
                                    raw_entry.get(
                                        "id"
                                    )
                                )
                                if (
                                    raw_entry.get(
                                        "id"
                                    )
                                    is not None
                                )
                                else None
                            ),

                        "title":
                            raw_entry.get(
                                "title"
                            ),

                        "duration":
                            entry_duration,

                        "thumbnail":
                            raw_entry.get(
                                "thumbnail"
                            ),

                        "formats":
                            entry_formats,

                        "media_type":
                            "video",

                        "media_url":
                            None,

                        "extension":
                            raw_entry.get(
                                "ext"
                            ),

                        "width":
                            raw_entry.get(
                                "width"
                            ),

                        "height":
                            raw_entry.get(
                                "height"
                            ),
                    }
                )

            # ------------------------------------------------
            # If caller selected one playlist item, also
            # expose that entry's formats at top level.
            #
            # This makes the response useful both for the
            # future multi-video UI and for normal quality
            # extraction code.
            # ------------------------------------------------

            top_formats: list[
                dict[str, Any]
            ] = []

            top_duration: (
                int
                | None
            ) = (
                _normalize_duration(
                    info.get(
                        "duration"
                    )
                )
            )

            top_thumbnail = (
                info.get(
                    "thumbnail"
                )
            )

            if (
                (
                    playlist_index
                    is not None
                    or story_playlist_index
                    is not None
                )
                and entries
            ):

                selected = (
                    entries[0]
                )

                top_formats = (
                    selected[
                        "formats"
                    ]
                )

                top_duration = (
                    selected.get(
                        "duration"
                    )
                )

                top_thumbnail = (
                    selected.get(
                        "thumbnail"
                    )
                    or top_thumbnail
                )

            return {
                "source_url":
                    source_url,

                "title":
                    info.get(
                        "title"
                    ),

                "duration":
                    top_duration,

                "thumbnail":
                    top_thumbnail,

                "formats":
                    top_formats,

                "media_type":
                    "video",

                "media_url":
                    None,

                "extension":
                    None,

                "width":
                    None,

                "height":
                    None,

                "is_playlist":
                    True,

                "entry_count":
                    len(
                        entries
                    ),

                "entries":
                    entries,
            }

        # ====================================================
        # Single media
        # ====================================================

        normalized_duration = (
            _normalize_duration(
                info.get(
                    "duration"
                )
            )
        )

        formats = (
            _normalize_formats(
                info.get(
                    "formats"
                ),
                duration=(
                    normalized_duration
                ),
            )
        )

        return {
            "source_url":
                source_url,

            "title":
                info.get(
                    "title"
                ),

            "duration":
                normalized_duration,

            "thumbnail":
                info.get(
                    "thumbnail"
                ),

            "formats":
                formats,

            "media_type":
                "video",

            "media_url":
                None,

            "extension":
                info.get(
                    "ext"
                ),

            "width":
                info.get(
                    "width"
                ),

            "height":
                info.get(
                    "height"
                ),

            "is_playlist":
                False,

            "entry_count":
                0,

            "entries":
                [],
        }
