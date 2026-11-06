from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
import json
import os
import selectors as io_selectors
import subprocess
import time
import requests

import yt_dlp
from sqlalchemy import select

from app.db.worker_session import WorkerSessionLocal
from app.services.instagram_formats import instagram_video_choices
from app.models.download_job import (
    DownloadJob,
    DownloadJobStatus,
)
from app.services.social_media import (
    BROWSER_USER_AGENT,
    extract_social_image,
    extract_threads_video,
    is_allowed_social_media_url,
    is_threads_url,
    social_platform,
    social_referer,
)
from app.services.media_formats import (
    normalize_output_format,
    output_format_kind,
)
from app.workers.celery_app import celery_app


# ============================================================
# Configuration
# ============================================================

DOWNLOAD_DIR = Path(
    "/app/downloads"
)

BGUTIL_POT_BASE_URL = os.getenv(
    "BGUTIL_POT_BASE_URL",
    "http://bgutil-provider:4416",
)

PAUSED_FILE_TTL_HOURS = 6

# Maximum allowed output size.
#
# Keep the worker-side limit even if the bot already checks
# estimated sizes. The worker is the final safety boundary.
MAX_DOWNLOAD_BYTES = (
    1900
    * 1024
    * 1024
)

UPLOAD_SOURCE_PREFIX = "upload://"


def _resolve_max_download_bytes(plan_limits_snapshot: object) -> int:
    snapshot = (
        plan_limits_snapshot
        if isinstance(plan_limits_snapshot, dict)
        else {}
    )
    try:
        max_file_size_mb = int(snapshot.get("max_file_size_mb") or 1900)
    except (TypeError, ValueError):
        max_file_size_mb = 1900

    return min(
        MAX_DOWNLOAD_BYTES,
        max(1, max_file_size_mb) * 1024 * 1024,
    )



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





IMAGE_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp",
    "gif",
    "avif",
}

IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/avif",
}


# ============================================================
# Control exceptions
# ============================================================

class DownloadPausedError(Exception):
    pass


class DownloadCancelledError(Exception):
    pass


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



# ============================================================
# Instagram image helpers
# ============================================================

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


def _is_allowed_instagram_cdn_url(
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

    if parsed.scheme != "https":

        return False

    allowed_hosts = (
        "cdninstagram.com",
        "fbcdn.net",
    )

    return any(
        hostname == domain
        or hostname.endswith(
            "." + domain
        )
        for domain
        in allowed_hosts
    )


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


def _extract_x_image(
    source_url: str,
    playlist_index: int | None,
) -> dict[str, Any]:

    if not _is_x_url(
        source_url
    ):

        raise ValueError(
            "Not an X/Twitter URL"
        )

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

    if result.returncode != 0:

        raise RuntimeError(
            "gallery-dl failed while extracting "
            "X image: "
            f"{result.stderr[-1000:]}"
        )

    try:

        payload = json.loads(
            result.stdout
        )

    except Exception as exc:

        raise RuntimeError(
            "Invalid gallery-dl JSON output for X"
        ) from exc

    if not isinstance(
        payload,
        list,
    ):

        raise RuntimeError(
            "Unexpected gallery-dl X output"
        )

    image_entries: list[
        dict[str, Any]
    ] = []

    for event in payload:

        if (
            not isinstance(
                event,
                list,
            )
            or len(
                event
            )
            < 3
            or event[0] != 3
        ):

            continue

        raw_url = event[1]
        metadata = event[2]

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
            _normalize_image_extension(
                metadata.get(
                    "extension"
                )
            )
        )

        if (
            media_kind
            not in {
                "photo",
                "image",
            }
            and extension is None
        ):

            continue

        if extension is None:

            continue

        if not _is_allowed_x_image_url(
            raw_url
        ):

            raise RuntimeError(
                "Extractor returned an unexpected "
                "X image host"
            )

        try:

            index = int(
                metadata.get(
                    "num",
                    1,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        image_entries.append(
            {
                "index":
                    index,

                "url":
                    raw_url,

                "extension":
                    extension,
            }
        )

    if not image_entries:

        raise RuntimeError(
            "No downloadable X images found"
        )

    if (
        playlist_index
        is not None
    ):

        selected = next(
            (
                item
                for item
                in image_entries
                if item[
                    "index"
                ]
                == playlist_index
            ),
            None,
        )

        if selected is None:

            raise RuntimeError(
                "Selected X media item "
                "is not an image"
            )

        return selected

    if len(
        image_entries
    ) == 1:

        return image_entries[0]

    raise ValueError(
        "playlist_index is required for "
        "an X post containing multiple images"
    )


def _normalize_image_extension(
    value: str | None,
) -> str | None:

    if not value:

        return None

    extension = (
        str(
            value
        )
        .strip()
        .lower()
        .lstrip(".")
    )

    if extension == "jpeg":

        extension = "jpg"

    if extension not in {
        "jpg",
        "png",
        "webp",
        "gif",
        "avif",
    }:

        return None

    return extension


def _image_extension_from_content_type(
    content_type: str | None,
) -> str | None:

    value = (
        str(
            content_type
            or ""
        )
        .split(
            ";",
            1,
        )[0]
        .strip()
        .lower()
    )

    mapping = {
        "image/jpeg":
            "jpg",

        "image/png":
            "png",

        "image/webp":
            "webp",

        "image/gif":
            "gif",

        "image/avif":
            "avif",
    }

    return mapping.get(
        value
    )


def _extract_instagram_image(
    source_url: str,
    playlist_index: int | None,
) -> dict[str, Any]:

    if not _is_instagram_url(
        source_url
    ):

        raise ValueError(
            "Image downloads currently support "
            "Instagram URLs only"
        )

    if _is_instagram_story_url(
        source_url
    ):

        raise ValueError(
            "Instagram Stories and Highlights "
            "are unavailable in public-only mode"
        )

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "socket_timeout": 15,
        "ignore_no_formats_error": True,
    }

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        raw_info = ydl.extract_info(
            source_url,
            download=False,
            process=False,
        )

    if not isinstance(
        raw_info,
        dict,
    ):

        raise RuntimeError(
            "Invalid Instagram extractor result"
        )

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

    image_entries: list[
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

        # Entries containing formats are videos.
        if item.get(
            "formats"
        ):

            continue

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

        if not thumbnails:

            thumbnail_url = (
                item.get(
                    "thumbnail"
                )
            )

            if not isinstance(
                thumbnail_url,
                str,
            ):

                continue

            best_thumbnail = {
                "url":
                    thumbnail_url
            }

        else:

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

            # yt-dlp normally orders Instagram thumbnails
            # from smaller to larger variants.
            #
            # Image-only posts may omit width/height metadata.
            # When all resolution scores are equal, prefer the
            # last candidate so the worker downloads the
            # highest-quality public image variant.
            best_thumbnail = max(
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

        raw_url = (
            best_thumbnail.get(
                "url"
            )
        )

        if not isinstance(
            raw_url,
            str,
        ):

            continue

        if not _is_allowed_instagram_cdn_url(
            raw_url
        ):

            raise RuntimeError(
                "Extractor returned an unexpected "
                "Instagram image host"
            )

        extension = (
            _normalize_image_extension(
                best_thumbnail.get(
                    "ext"
                )
                or item.get(
                    "ext"
                )
            )
        )

        if extension is None:

            try:

                extension = (
                    _normalize_image_extension(
                        Path(
                            urlparse(
                                raw_url
                            ).path
                        ).suffix.lstrip(
                            "."
                        )
                    )
                )

            except Exception:

                extension = None

        if extension is None:

            extension = "jpg"

        image_entries.append(
            {
                "index":
                    index,

                "url":
                    raw_url,

                "extension":
                    extension,
            }
        )

    if not image_entries:

        raise RuntimeError(
            "No downloadable Instagram images found"
        )

    if playlist_index is not None:

        selected = next(
            (
                item
                for item
                in image_entries
                if item[
                    "index"
                ]
                == playlist_index
            ),
            None,
        )

        if selected is None:

            raise RuntimeError(
                "Selected Instagram carousel "
                "item is not an image"
            )

        return selected

    if (
        len(
            raw_entries
        )
        == 1
        and len(
            image_entries
        )
        == 1
    ):

        return image_entries[0]

    raise ValueError(
        "playlist_index is required for an "
        "Instagram post containing multiple items"
    )


def _download_social_image(
    job_id: int,
    source_url: str,
    playlist_index: int | None,
    max_download_bytes: int,
) -> Path:

    if _is_instagram_url(
        source_url
    ):

        item = (
            _extract_instagram_image(
                source_url=source_url,
                playlist_index=(
                    playlist_index
                ),
            )
        )

        url_validator = (
            _is_allowed_instagram_cdn_url
        )

        referer = (
            "https://www.instagram.com/"
        )

        platform_name = "Instagram"

    elif _is_x_url(
        source_url
    ):

        item = (
            _extract_x_image(
                source_url=source_url,
                playlist_index=(
                    playlist_index
                ),
            )
        )

        url_validator = (
            _is_allowed_x_image_url
        )

        referer = (
            "https://x.com/"
        )

        platform_name = "X"

    elif social_platform(source_url) in {
        "pinterest",
        "tiktok",
        "facebook",
        "threads",
    }:

        item = extract_social_image(
            source_url=source_url,
            playlist_index=playlist_index,
        )

        platform = str(item["platform"])
        url_validator = lambda url: is_allowed_social_media_url(
            platform,
            url,
            "image",
        )
        referer = social_referer(platform)
        platform_name = platform.title()

    else:

        raise ValueError(
            "Image downloads currently support "
            "Instagram, X, Pinterest, TikTok, "
            "Facebook and Threads URLs"
        )

    media_url = str(
        item[
            "url"
        ]
    )

    metadata_extension = (
        _normalize_image_extension(
            item.get(
                "extension"
            )
        )
    )

    if metadata_extension is None:

        raise RuntimeError(
            "Unsupported image extension"
        )

    part_path = (
        DOWNLOAD_DIR
        / (
            f"{job_id}."
            f"{metadata_extension}"
            ".part"
        )
    )

    existing_bytes = 0

    if part_path.is_file():

        existing_bytes = (
            part_path
            .stat()
            .st_size
        )

        if (
            existing_bytes
            > max_download_bytes
        ):

            raise RuntimeError(
                "Partial image exceeds "
                "maximum allowed size"
            )

    headers = {
        "User-Agent":
            (
                "Mozilla/5.0 "
                "(X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),

        "Referer":
            referer,
    }

    if existing_bytes > 0:

        headers[
            "Range"
        ] = (
            f"bytes={existing_bytes}-"
        )

    response = requests.get(
        media_url,
        headers=headers,
        stream=True,
        timeout=(
            15,
            60,
        ),
        allow_redirects=True,
    )

    # --------------------------------------------------------
    # Validate final redirect target as well.
    # --------------------------------------------------------

    if not url_validator(
        response.url
    ):

        response.close()

        raise RuntimeError(
            f"{platform_name} image redirected to "
            "an unexpected host"
        )

    # --------------------------------------------------------
    # A stale/finished range can produce 416.
    # Retry once from byte zero.
    # --------------------------------------------------------

    if (
        existing_bytes > 0
        and response.status_code == 416
    ):

        response.close()

        part_path.unlink(
            missing_ok=True
        )

        existing_bytes = 0

        headers.pop(
            "Range",
            None,
        )

        response = requests.get(
            media_url,
            headers=headers,
            stream=True,
            timeout=(
                15,
                60,
            ),
            allow_redirects=True,
        )

        if not url_validator(
            response.url
        ):

            response.close()

            raise RuntimeError(
                f"{platform_name} image redirected to "
                "an unexpected host"
            )

    response.raise_for_status()

    content_type = (
        response.headers.get(
            "Content-Type",
            ""
        )
        .split(
            ";",
            1,
        )[0]
        .strip()
        .lower()
    )

    if (
        content_type
        not in IMAGE_CONTENT_TYPES
    ):

        response.close()

        raise RuntimeError(
            "Unexpected image Content-Type: "
            f"{content_type or 'unknown'}"
        )

    response_extension = (
        _image_extension_from_content_type(
            content_type
        )
    )

    extension = (
        response_extension
        or metadata_extension
    )

    if extension is None:

        response.close()

        raise RuntimeError(
            "Could not determine image extension"
        )

    # If server ignored Range and returned 200,
    # restart the partial file from zero.
    append_mode = (
        existing_bytes > 0
        and response.status_code == 206
    )

    if not append_mode:

        existing_bytes = 0

    content_length = (
        response.headers.get(
            "Content-Length"
        )
    )

    total_bytes: int | None = None

    if content_length:

        try:

            remaining = int(
                content_length
            )

            total_bytes = (
                existing_bytes
                + remaining
            )

        except (
            TypeError,
            ValueError,
        ):

            total_bytes = None

    if (
        total_bytes is not None
        and total_bytes
        > max_download_bytes
    ):

        response.close()

        raise RuntimeError(
            "Image exceeds the plan file-size limit"
        )

    mode = (
        "ab"
        if append_mode
        else "wb"
    )

    downloaded_bytes = (
        existing_bytes
    )

    last_reported_bytes = (
        downloaded_bytes
    )

    last_reported_progress = -1

    try:

        with part_path.open(
            mode
        ) as output:

            for chunk in (
                response.iter_content(
                    chunk_size=(
                        256
                        * 1024
                    )
                )
            ):

                if not chunk:

                    continue

                _check_job_control(
                    job_id
                )

                output.write(
                    chunk
                )

                downloaded_bytes += (
                    len(
                        chunk
                    )
                )

                if (
                    downloaded_bytes
                    > max_download_bytes
                ):

                    raise RuntimeError(
                        "Image exceeds the plan file-size limit"
                    )

                if (
                    total_bytes
                    and total_bytes > 0
                ):

                    progress = int(
                        (
                            downloaded_bytes
                            / total_bytes
                        )
                        * 100
                    )

                    progress = max(
                        0,
                        min(
                            progress,
                            99,
                        ),
                    )

                else:

                    progress = 0

                if (
                    progress
                    != last_reported_progress
                    or (
                        downloaded_bytes
                        - last_reported_bytes
                    )
                    >= (
                        1024
                        * 1024
                    )
                ):

                    _update_download_stats(
                        job_id=job_id,
                        progress=progress,
                        downloaded_bytes=(
                            downloaded_bytes
                        ),
                        total_bytes=(
                            total_bytes
                        ),
                        speed=None,
                        eta=None,
                    )

                    last_reported_progress = (
                        progress
                    )

                    last_reported_bytes = (
                        downloaded_bytes
                    )

    finally:

        response.close()

    _check_job_control(
        job_id
    )

    if (
        not part_path.is_file()
        or part_path.stat().st_size <= 0
    ):

        raise RuntimeError(
            "Downloaded image is empty"
        )

    final_path = (
        DOWNLOAD_DIR
        / (
            f"{job_id}."
            f"{extension}"
        )
    )

    # If metadata extension differs from MIME extension,
    # move the completed partial file to the correct suffix.
    os.replace(
        part_path,
        final_path,
    )

    return final_path


# ============================================================
# Quality helpers
# ============================================================

def _get_quality_height(
    quality: str | None,
) -> int | None:

    if not quality:
        return None

    value = (
        str(
            quality
        )
        .strip()
        .lower()
    )

    aliases = {
        "2k": 1440,
        "4k": 2160,
    }

    if value in aliases:

        return (
            aliases[
                value
            ]
        )

    value = (
        value
        .replace(
            "p",
            "",
        )
        .strip()
    )

    if not value.isdigit():
        return None

    result = int(
        value
    )

    if result <= 0:
        return None

    return result


def _get_format_short_side(
    item: dict,
) -> int | None:

    width = item.get(
        "width"
    )

    height = item.get(
        "height"
    )

    if (
        width is None
        or height is None
    ):
        return None

    try:

        width = int(
            width
        )

        height = int(
            height
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if (
        width <= 0
        or height <= 0
    ):
        return None

    return min(
        width,
        height,
    )


# ============================================================
# yt-dlp options
# ============================================================

def _get_yt_dlp_options(
    source_url: str,
    **options: Any,
) -> dict:

    ydl_options = dict(
        options
    )

    if _is_instagram_story_url(
        source_url
    ):

        raise ValueError(
            "Instagram Stories and Highlights "
            "are unavailable in public-only mode"
        )

    if not _is_youtube_url(
        source_url
    ):

        return ydl_options

    # --------------------------------------------------------
    # JavaScript runtime
    # --------------------------------------------------------

    ydl_options[
        "js_runtimes"
    ] = {
        "deno": {},
    }

    # --------------------------------------------------------
    # EJS challenge solver
    # --------------------------------------------------------

    ydl_options[
        "remote_components"
    ] = {
        "ejs:github",
    }

    # --------------------------------------------------------
    # YouTube mweb + bgutil PO Token
    # --------------------------------------------------------

    ydl_options[
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

    return ydl_options


# ============================================================
# Job state
# ============================================================

def _get_job_status(
    job_id: int,
) -> DownloadJobStatus | None:

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:
            return None

        return job.status


def _check_job_control(
    job_id: int,
) -> None:

    status = (
        _get_job_status(
            job_id
        )
    )

    if (
        status
        == DownloadJobStatus.PAUSED
    ):

        raise DownloadPausedError(
            f"Download job {job_id} paused"
        )

    if (
        status
        == DownloadJobStatus.CANCELLED
    ):

        raise DownloadCancelledError(
            f"Download job {job_id} cancelled"
        )

    if (
        status
        == DownloadJobStatus.EXPIRED
    ):

        raise DownloadCancelledError(
            f"Download job {job_id} expired"
        )


# ============================================================
# Database - live stats
# ============================================================

def _update_download_stats(
    job_id: int,
    progress: int,
    downloaded_bytes: int | None,
    total_bytes: int | None,
    speed: float | None,
    eta: int | None,
) -> None:

    progress = max(
        0,
        min(
            int(
                progress
            ),
            99,
        ),
    )

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:
            return

        if job.status not in (
            DownloadJobStatus.PENDING,
            DownloadJobStatus.PROCESSING,
        ):
            return

        job.progress = (
            progress
        )

        if (
            downloaded_bytes
            is not None
            and downloaded_bytes >= 0
        ):

            job.downloaded_bytes = int(
                downloaded_bytes
            )

        if (
            total_bytes
            is not None
            and total_bytes > 0
        ):

            job.total_bytes = int(
                total_bytes
            )

        if (
            speed is not None
            and speed >= 0
        ):

            job.speed = float(
                speed
            )

        else:

            job.speed = None

        if (
            eta is not None
            and eta >= 0
        ):

            job.eta = int(
                eta
            )

        else:

            job.eta = None

        session.commit()


# ============================================================
# Database - processing
# ============================================================

def _set_processing(
    job_id: int,
) -> bool:

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:
            return False

        if job.status in (
            DownloadJobStatus.PAUSED,
            DownloadJobStatus.CANCELLED,
            DownloadJobStatus.EXPIRED,
            DownloadJobStatus.COMPLETED,
        ):

            return False

        job.status = (
            DownloadJobStatus.PROCESSING
        )

        job.error_message = None

        if job.started_at is None:

            job.started_at = (
                datetime.now(
                    timezone.utc
                )
            )

        session.commit()

        return True


# ============================================================
# Database - completed
# ============================================================

def _set_completed(
    job_id: int,
    file_path: str,
    file_size: int,
) -> None:

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:
            return

        if job.status in (
            DownloadJobStatus.PAUSED,
            DownloadJobStatus.CANCELLED,
            DownloadJobStatus.EXPIRED,
        ):

            return

        job.status = (
            DownloadJobStatus.COMPLETED
        )

        job.progress = 100

        job.file_path = (
            file_path
        )

        job.file_size = (
            file_size
        )

        # ----------------------------------------------------
        # Final values
        #
        # Multi-stream sites such as Instagram may report
        # video/audio stream totals separately while
        # downloading. Once the merge is complete, the final
        # file size is the authoritative value.
        # ----------------------------------------------------

        job.downloaded_bytes = (
            file_size
        )

        job.total_bytes = (
            file_size
        )

        job.speed = None

        job.eta = 0

        job.error_message = None

        job.paused_at = None

        job.completed_at = (
            datetime.now(
                timezone.utc
            )
        )

        session.commit()


# ============================================================
# Database - failed
# ============================================================

def _set_failed(
    job_id: int,
    error_message: str,
) -> None:

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:
            return

        if job.status in (
            DownloadJobStatus.PAUSED,
            DownloadJobStatus.CANCELLED,
            DownloadJobStatus.EXPIRED,
        ):

            return

        job.status = (
            DownloadJobStatus.FAILED
        )

        job.error_message = (
            str(
                error_message
            )[:5000]
        )

        job.speed = None

        job.eta = None

        session.commit()


# ============================================================
# Database - expired
# ============================================================

def _set_expired(
    job_id: int,
) -> None:

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:
            return

        if (
            job.status
            != DownloadJobStatus.PAUSED
        ):

            return

        job.status = (
            DownloadJobStatus.EXPIRED
        )

        job.expired_at = (
            datetime.now(
                timezone.utc
            )
        )

        job.speed = None

        job.eta = None

        session.commit()


# ============================================================
# File cleanup
# ============================================================

def _cleanup_job_files(
    job_id: int,
) -> None:

    DOWNLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for path in DOWNLOAD_DIR.glob(
        f"{job_id}.*"
    ):

        if not path.is_file():
            continue

        try:

            path.unlink()

            print(
                "Deleted job file: "
                f"{path}"
            )

        except FileNotFoundError:

            pass

        except Exception as exc:

            print(
                "Failed to delete job file "
                f"{path}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


# ============================================================
# ffprobe
# ============================================================

def _probe_media_file(
    file_path: Path,
) -> dict | None:

    if not file_path.exists():

        return None

    if not file_path.is_file():

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
                str(
                    file_path
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    except (
        FileNotFoundError,
        subprocess.SubprocessError,
        OSError,
    ):

        return None

    if (
        result.returncode
        != 0
    ):

        print(
            "ffprobe failed for "
            f"{file_path}: "
            f"{result.stderr.strip()}"
        )

        return None

    stdout = (
        result.stdout
        .strip()
    )

    if not stdout:

        return None

    try:

        data = json.loads(
            stdout
        )

    except json.JSONDecodeError:

        return None

    if not isinstance(
        data,
        dict,
    ):

        return None

    return data


def _get_video_resolution(
    file_path: Path,
) -> tuple[
    int,
    int,
] | None:

    probe = (
        _probe_media_file(
            file_path
        )
    )

    if probe is None:

        return None

    streams = (
        probe.get(
            "streams",
            [],
        )
    )

    for stream in streams:

        if (
            stream.get(
                "codec_type"
            )
            != "video"
        ):

            continue

        width = stream.get(
            "width"
        )

        height = stream.get(
            "height"
        )

        try:

            width = int(
                width
            )

            height = int(
                height
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        if (
            width <= 0
            or height <= 0
        ):

            continue

        return (
            width,
            height,
        )

    return None


def _has_audio_stream(
    file_path: Path,
) -> bool:

    probe = (
        _probe_media_file(
            file_path
        )
    )

    if probe is None:

        return False

    for stream in probe.get(
        "streams",
        [],
    ):

        if (
            stream.get(
                "codec_type"
            )
            == "audio"
        ):

            return True

    return False


# ============================================================
# FFmpeg conversion
# ============================================================

def _resolve_uploaded_source(source_url: str) -> Path | None:
    """Resolve a bot-uploaded file without allowing path traversal."""

    value = str(source_url or "")
    if not value.startswith(UPLOAD_SOURCE_PREFIX):
        return None

    name = value.removeprefix(UPLOAD_SOURCE_PREFIX)
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in name)
    ):
        raise RuntimeError("Invalid uploaded media reference")

    incoming_dir = (DOWNLOAD_DIR / "incoming").resolve()
    candidate = (incoming_dir / name).resolve()
    if candidate.parent != incoming_dir:
        raise RuntimeError("Invalid uploaded media path")
    if not candidate.is_file():
        raise FileNotFoundError("Uploaded media file is no longer available")
    return candidate


def _media_duration_seconds(probe: dict | None) -> float | None:
    if not isinstance(probe, dict):
        return None
    format_info = probe.get("format")
    if not isinstance(format_info, dict):
        return None
    try:
        duration = float(format_info.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def _build_ffmpeg_conversion_command(
    input_path: Path,
    output_path: Path,
    output_format: str,
    probe: dict | None = None,
) -> list[str]:
    """Build a deterministic, allow-listed FFmpeg conversion command."""

    normalized = normalize_output_format(output_format)
    if normalized is None:
        raise ValueError("Unsupported output format")

    streams = probe.get("streams", []) if isinstance(probe, dict) else []
    has_video = any(
        isinstance(stream, dict) and stream.get("codec_type") == "video"
        for stream in streams
    )
    has_audio = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in streams
    )

    kind = output_format_kind(normalized)
    if kind == "audio" and not has_audio:
        raise RuntimeError("The input file has no audio stream")
    if kind == "video" and not has_video:
        raise RuntimeError("The input file has no video stream")

    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(input_path),
    ]

    if kind == "audio":
        command.extend(["-map", "0:a:0", "-vn"])
        audio_args = {
            "mp3": ["-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3"],
            "m4a": ["-c:a", "aac", "-b:a", "192k", "-f", "ipod"],
            "wav": ["-c:a", "pcm_s16le", "-f", "wav"],
            "aac": ["-c:a", "aac", "-b:a", "192k", "-f", "adts"],
            "flac": ["-c:a", "flac", "-f", "flac"],
            "ogg": ["-c:a", "libvorbis", "-q:a", "5", "-f", "ogg"],
            "opus": ["-c:a", "libopus", "-b:a", "128k", "-f", "opus"],
        }
        command.extend(audio_args[normalized])
    else:
        command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
        video_args = {
            "mp4": [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-f", "mp4",
            ],
            "mkv": [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-f", "matroska",
            ],
            "avi": [
                "-c:v", "mpeg4", "-q:v", "5", "-c:a", "libmp3lame",
                "-b:a", "128k", "-f", "avi",
            ],
            "mov": [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-f", "mov",
            ],
            "webm": [
                "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32",
                "-c:a", "libopus", "-b:a", "128k", "-f", "webm",
            ],
        }
        command.extend(video_args[normalized])

    command.extend([
        "-progress", "pipe:1",
        "-nostats",
        str(output_path),
    ])
    return command


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_ffmpeg_conversion(
    *,
    job_id: int,
    input_path: Path,
    output_format: str,
    max_download_bytes: int,
) -> Path:
    """Convert one local media file and report progress to the Job row."""

    normalized = normalize_output_format(output_format)
    if normalized is None:
        raise ValueError("Unsupported output format")

    probe = _probe_media_file(input_path)
    if probe is None:
        raise RuntimeError("The input file is not a readable audio/video file")

    duration = _media_duration_seconds(probe)
    temporary_path = DOWNLOAD_DIR / f"{job_id}.converted.{normalized}.part"
    final_path = DOWNLOAD_DIR / f"{job_id}.{normalized}"
    temporary_path.unlink(missing_ok=True)
    if final_path != input_path:
        final_path.unlink(missing_ok=True)

    command = _build_ffmpeg_conversion_command(
        input_path=input_path,
        output_path=temporary_path,
        output_format=normalized,
        probe=probe,
    )
    print(
        "Starting FFmpeg conversion: "
        f"job={job_id}, input={input_path.name}, output={normalized}"
    )

    try:
        process: subprocess.Popen[str] = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is not available for media conversion") from exc
    except OSError as exc:
        raise RuntimeError("Unable to start FFmpeg media conversion") from exc

    selector = io_selectors.DefaultSelector()
    if process.stdout is not None:
        selector.register(process.stdout, io_selectors.EVENT_READ)
    started = time.monotonic()
    last_update = 0.0

    try:
        while process.poll() is None:
            events = selector.select(timeout=0.5)
            out_time_seconds: float | None = None
            for key, _ in events:
                line = key.fileobj.readline()
                if not line:
                    continue
                field, _, value = line.strip().partition("=")
                if field == "out_time_ms":
                    try:
                        # FFmpeg reports this value in microseconds.
                        out_time_seconds = max(0.0, float(value) / 1_000_000)
                    except ValueError:
                        pass

            _check_job_control(job_id)
            if temporary_path.exists() and temporary_path.stat().st_size > max_download_bytes:
                raise RuntimeError("Converted output exceeds the plan file-size limit")

            now = time.monotonic()
            if now - last_update >= 1.0:
                progress = 0
                if duration and out_time_seconds is not None:
                    progress = min(99, max(0, int(out_time_seconds / duration * 100)))
                output_size = temporary_path.stat().st_size if temporary_path.exists() else 0
                speed = output_size / max(now - started, 0.001)
                _update_download_stats(
                    job_id=job_id,
                    progress=progress,
                    downloaded_bytes=output_size,
                    total_bytes=None,
                    speed=speed,
                    eta=(
                        max(1, int((duration - out_time_seconds) / max(out_time_seconds / max(now - started, 0.001), 0.001)))
                        if duration and out_time_seconds and out_time_seconds < duration
                        else None
                    ),
                )
                last_update = now

        stdout_tail = process.stdout.read() if process.stdout is not None else ""
        stderr_text = process.stderr.read() if process.stderr is not None else ""
        return_code = process.wait()
    except (DownloadPausedError, DownloadCancelledError):
        _terminate_process(process)
        raise
    except Exception:
        _terminate_process(process)
        raise
    finally:
        selector.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    if return_code != 0:
        temporary_path.unlink(missing_ok=True)
        detail = (stderr_text or stdout_tail).strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        raise RuntimeError("FFmpeg media conversion failed" + (f": {detail}" if detail else ""))
    if not temporary_path.exists() or temporary_path.stat().st_size <= 0:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg conversion did not create a valid output file")
    if temporary_path.stat().st_size > max_download_bytes:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError("Converted output exceeds the plan file-size limit")

    temporary_path.replace(final_path)
    if input_path != final_path:
        _cleanup_uploaded_source(input_path)
    print(f"FFmpeg conversion completed: job={job_id}, file={final_path}")
    return final_path


def _cleanup_uploaded_source(source_path: Path | None) -> None:
    if source_path is None:
        return
    try:
        source_path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"Failed to remove uploaded source {source_path}: {exc}")


def _cleanup_uploaded_source_reference(source_url: str | None) -> None:
    """Best-effort cleanup for jobs cancelled before the worker starts."""

    if not str(source_url or "").startswith(UPLOAD_SOURCE_PREFIX):
        return
    try:
        _cleanup_uploaded_source(_resolve_uploaded_source(str(source_url)))
    except (FileNotFoundError, RuntimeError):
        # Missing or already-invalid staged files need no further action.
        return


# ============================================================
# iOS / Instagram video compatibility
# ============================================================

def _ios_streams(probe: dict | None) -> tuple[dict, dict]:
    streams = (probe or {}).get("streams") or []
    return (
        next((s for s in streams if s.get("codec_type") == "video"), {}),
        next((s for s in streams if s.get("codec_type") == "audio"), {}),
    )


def _ios_video_compatible(stream: dict) -> bool:
    return stream.get("codec_name") == "h264" and stream.get("pix_fmt") == "yuv420p"


def _ios_audio_compatible(stream: dict) -> bool:
    return stream.get("codec_name") == "aac" and stream.get("profile") == "LC"


def _normalize_video_for_compatibility(
    job_id: int, file_path: Path, max_download_bytes: int,
) -> Path:
    started = time.monotonic()
    source_size = file_path.stat().st_size
    if source_size <= 0 or source_size > max_download_bytes:
        return file_path  # The caller rejects empty and over-limit files.

    probe = _probe_media_file(file_path)
    video, audio = _ios_streams(probe)
    if not video:
        raise RuntimeError("Unable to inspect video for compatibility conversion")
    format_info = (probe or {}).get("format") or {}
    copy_video = _ios_video_compatible(video)
    copy_audio = _ios_audio_compatible(audio)
    mp4 = file_path.suffix.lower() == ".mp4" and "mp4" in (
        format_info.get("format_name") or ""
    ).split(",")
    compatible = mp4 and copy_video and (not audio or copy_audio)
    print(
        f"Video compatibility check: job={job_id}, codec={video.get('codec_name')}, "
        f"pix_fmt={video.get('pix_fmt')}, compatible={compatible}"
    )
    if compatible:
        print(f"Video compatibility ready: job={job_id}, mode=passthrough, elapsed_s={time.monotonic()-started:.2f}")
        return file_path

    mode = "remux" if copy_video else "transcode_video"
    if copy_video and audio and not copy_audio:
        mode = "transcode_audio"
    audio_options = (
        ["-an"] if not audio else ["-c:a", "copy"] if copy_audio else
        ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k"]
    )
    video_options = ["-c:v", "copy"]
    if not copy_video:
        # Keep the bitrate budget close to the source even for efficient VP9.
        # The preset changes encoding effort, not the frame size or frame rate.
        try:
            duration = float(format_info.get("duration") or 0)
        except (ValueError, TypeError):
            duration = 0
        rate_options = ["-crf", "20"]
        if duration > 0:
            audio_rate = 128000 if audio else 0
            if copy_audio:
                try:
                    measured_rate = int(audio.get("bit_rate") or 0)
                    if measured_rate > 0:
                        audio_rate = measured_rate
                except (ValueError, TypeError):
                    pass
            target = max(250000, int(source_size * 8 / duration * 0.96) - audio_rate)
            rate_options = ["-b:v", str(target), "-maxrate", str(target * 3 // 2), "-bufsize", str(target * 2)]
            print(
                f"Video compatibility size control: job={job_id}, source_size={source_size}, "
                f"duration={duration}, target_video_bitrate={target}"
            )
        video_options = ["-c:v", "libx264", "-preset", "veryfast", *rate_options, "-pix_fmt", "yuv420p"]

    temporary = DOWNLOAD_DIR / f"{job_id}.ios-normalized.mp4.part"
    final = DOWNLOAD_DIR / f"{job_id}.mp4"
    print(f"Video compatibility start: job={job_id}, mode={mode}")
    try:
        command = [
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(file_path),
            "-map", "0:v:0", "-map", "0:a:0?", *video_options, *audio_options,
            "-movflags", "+faststart", "-f", "mp4", str(temporary),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("iOS video normalization failed: " + result.stderr.strip()[-2000:])
        new_size = temporary.stat().st_size
        if not 0 < new_size <= max_download_bytes:
            raise RuntimeError("iOS normalized video is empty or exceeds the plan file-size limit")
        out_video, out_audio = _ios_streams(_probe_media_file(temporary))
        if (not _ios_video_compatible(out_video) or bool(audio) != bool(out_audio)
                or (out_audio and not _ios_audio_compatible(out_audio))):
            raise RuntimeError("iOS normalized video failed stream validation")
        temporary.replace(final)
    finally:
        temporary.unlink(missing_ok=True)
    if file_path != final:
        try:
            file_path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"Unable to delete original Instagram input: job={job_id}, error={type(exc).__name__}")
    print(f"Video compatibility ready: job={job_id}, mode={mode}, elapsed_s={time.monotonic()-started:.2f}")
    return final


def _prepare_requested_output(
    job_id: int, file_path: Path, media_type: str | None,
    output_format: str | None, max_download_bytes: int,
) -> Path:
    # An MP4 video request is the opt-in H.264/AAC compatible version.
    # Ordinary video downloads retain their original streams.
    if media_type == "video" and output_format == "mp4":
        return _normalize_video_for_compatibility(job_id, file_path, max_download_bytes)
    if output_format:
        return _run_ffmpeg_conversion(
            job_id=job_id, input_path=file_path, output_format=output_format,
            max_download_bytes=max_download_bytes,
        )
    print(f"Media output ready: job={job_id}, mode=original")
    return file_path


# ============================================================
# Find final output
# ============================================================

def _find_final_output_file(
    job_id: int,
    media_type: str | None,
) -> Path:

    candidates: list[
        Path
    ] = []

    for path in DOWNLOAD_DIR.glob(
        f"{job_id}.*"
    ):

        if not path.is_file():
            continue

        name = (
            path.name
            .lower()
        )

        if name.endswith(
            ".part"
        ):
            continue

        if name.endswith(
            ".ytdl"
        ):
            continue

        candidates.append(
            path
        )

    if not candidates:

        raise RuntimeError(
            "Download completed "
            "but no output files were found"
        )

    print(
        "Output candidates for job "
        f"{job_id}: "
        + ", ".join(
            str(
                path
            )
            for path
            in candidates
        )
    )

    # ========================================================
    # Audio job
    # ========================================================

    if (
        media_type
        == "audio"
    ):

        audio_candidates: list[
            Path
        ] = []

        for path in candidates:

            if _has_audio_stream(
                path
            ):

                audio_candidates.append(
                    path
                )

        if audio_candidates:

            return max(
                audio_candidates,
                key=lambda item: (
                    item.stat().st_size
                ),
            )

        return max(
            candidates,
            key=lambda item: (
                item.stat().st_size
            ),
        )

    # ========================================================
    # Video job
    #
    # IMPORTANT:
    # Only accept files that ffprobe confirms contain an
    # actual video stream.
    # ========================================================

    video_candidates: list[
        Path
    ] = []

    for path in candidates:

        resolution = (
            _get_video_resolution(
                path
            )
        )

        if resolution is None:

            print(
                "Ignoring non-video output candidate: "
                f"{path}"
            )

            continue

        print(
            "Valid video output candidate: "
            f"{path}, "
            f"resolution="
            f"{resolution[0]}x"
            f"{resolution[1]}"
        )

        video_candidates.append(
            path
        )

    if not video_candidates:

        raise RuntimeError(
            "Download completed but no "
            "valid video output file was found"
        )

    # --------------------------------------------------------
    # Prefer a file containing both video and audio.
    # --------------------------------------------------------

    video_audio_candidates = [
        path
        for path
        in video_candidates
        if _has_audio_stream(
            path
        )
    ]

    if video_audio_candidates:

        return max(
            video_audio_candidates,
            key=lambda item: (
                item.stat().st_size
            ),
        )

    return max(
        video_candidates,
        key=lambda item: (
            item.stat().st_size
        ),
    )


# ============================================================
# Non-YouTube format selection
# ============================================================

def _get_non_youtube_format_selector(
    source_url: str,
    requested_quality: int,
    playlist_index: int | None = None,
) -> str:

    ydl_options = (
        _get_yt_dlp_options(
            source_url,
            quiet=True,
            no_warnings=True,
            skip_download=True,
            noplaylist=(
                playlist_index
                is None
            ),
        )
    )

    if (
        playlist_index
        is not None
    ):

        ydl_options[
            "playlist_items"
        ] = str(
            playlist_index
        )

    with yt_dlp.YoutubeDL(
        ydl_options
    ) as ydl:

        info = ydl.extract_info(
            source_url,
            download=False,
        )

    if (
        info.get(
            "_type"
        )
        == "playlist"
    ):

        entries = [
            entry
            for entry
            in (
                info.get(
                    "entries"
                )
                or []
            )
            if isinstance(
                entry,
                dict,
            )
        ]

        if not entries:

            raise RuntimeError(
                "Selected playlist item "
                "does not contain media"
            )

        info = (
            entries[0]
        )

    formats = info.get(
        "formats",
        [],
    )

    if _is_instagram_url(source_url):
        choices = instagram_video_choices(formats)
        if not choices:
            raise RuntimeError(f"No usable Instagram video format found for quality {requested_quality}p")
        quality = min(choices, key=lambda q: (abs(q - requested_quality), -q))
        chosen, selector = choices[quality]
        print(f"Selected Instagram format: quality={quality}p, codec={chosen.get('vcodec')}, id={chosen['format_id']}")
        return selector

    usable_candidates: list[
        dict
    ] = []

    for item in formats:

        vcodec = (
            item.get(
                "vcodec"
            )
        )

        short_side = (
            _get_format_short_side(
                item
            )
        )

        # ----------------------------------------------------
        # X / Twitter direct HTTP formats may have:
        #
        # vcodec=None
        # acodec=None
        # width/height=<valid values>
        #
        # Valid dimensions are enough to classify them
        # as video.
        # ----------------------------------------------------

        if (
            vcodec
            == "images"
        ):

            continue

        if (
            short_side
            is None
        ):

            continue

        usable_candidates.append(
            item
        )

    if not usable_candidates:

        raise RuntimeError(
            "No usable video format "
            f"found for quality "
            f"{requested_quality}p"
        )

    # --------------------------------------------------------
    # Find nearest real resolution.
    #
    # Examples:
    #
    # requested 360p
    # available 320p, 364p
    #
    # 364 is only 4 pixels away -> choose 364.
    #
    # If two resolutions are equally close, prefer the
    # higher one.
    # --------------------------------------------------------

    nearest_quality = min(
        (
            _get_format_short_side(
                item
            )
            or 0
        )
        for item
        in usable_candidates
    )

    nearest_distance = abs(
        nearest_quality
        - requested_quality
    )

    for item in usable_candidates:

        candidate_quality = (
            _get_format_short_side(
                item
            )
            or 0
        )

        distance = abs(
            candidate_quality
            - requested_quality
        )

        if (
            distance
            < nearest_distance
        ):

            nearest_quality = (
                candidate_quality
            )

            nearest_distance = (
                distance
            )

        elif (
            distance
            == nearest_distance
            and candidate_quality
            > nearest_quality
        ):

            nearest_quality = (
                candidate_quality
            )

    candidates = [
        item
        for item
        in usable_candidates
        if (
            _get_format_short_side(
                item
            )
            == nearest_quality
        )
    ]

    # --------------------------------------------------------
    # Candidate scoring
    # --------------------------------------------------------

    def candidate_score(
        item: dict,
    ) -> tuple:

        extension = str(
            item.get(
                "ext"
            )
            or ""
        ).lower()

        vcodec = str(
            item.get(
                "vcodec"
            )
            or ""
        ).lower()

        protocol = str(
            item.get(
                "protocol"
            )
            or ""
        ).lower()

        tbr = (
            item.get(
                "tbr"
            )
            or 0
        )

        filesize = (
            item.get(
                "filesize"
            )
            or item.get(
                "filesize_approx"
            )
            or 0
        )

        try:

            tbr = float(
                tbr
            )

        except (
            TypeError,
            ValueError,
        ):

            tbr = 0.0

        try:

            filesize = int(
                filesize
            )

        except (
            TypeError,
            ValueError,
        ):

            filesize = 0

        return (
            1
            if extension == "mp4"
            else 0,

            1
            if vcodec.startswith(
                "avc1"
            )
            else 0,

            1
            if "http" in protocol
            else 0,

            tbr,

            filesize,
        )

    selected_format = max(
        candidates,
        key=candidate_score,
    )

    selected_format_id = str(
        selected_format.get(
            "format_id"
        )
    )

    selected_width = (
        selected_format.get(
            "width"
        )
    )

    selected_height = (
        selected_format.get(
            "height"
        )
    )

    selected_quality = (
        _get_format_short_side(
            selected_format
        )
    )

    has_audio = (
        selected_format.get(
            "acodec"
        )
        not in (
            None,
            "none",
        )
    )

    print(
        "Selected non-YouTube format: "
        f"id={selected_format_id}, "
        f"resolution="
        f"{selected_width}x"
        f"{selected_height}, "
        f"quality="
        f"{selected_quality}p, "
        f"requested="
        f"{requested_quality}p, "
        f"has_audio="
        f"{has_audio}"
    )

    # --------------------------------------------------------
    # Progressive
    # --------------------------------------------------------

    if has_audio:

        return (
            selected_format_id
        )

    # --------------------------------------------------------
    # Video-only + audio
    # --------------------------------------------------------

    return (
        f"{selected_format_id}"
        "+"
        "bestaudio[ext=m4a]"
        "/"
        f"{selected_format_id}"
        "+"
        "bestaudio"
    )


# ============================================================
# Format selector
# ============================================================

def _instagram_format_selector(requested_quality: int) -> Callable:
    """Select from yt-dlp's current extraction, without requesting the post twice."""
    def select_format(context: dict):
        formats = context.get("formats") or []
        choices = instagram_video_choices(formats)
        if not choices:
            raise RuntimeError(f"No usable Instagram video format found for quality {requested_quality}p")
        quality = min(choices, key=lambda q: (abs(q - requested_quality), -q))
        video, _ = choices[quality]
        audio_formats = [
            f for f in formats if f.get("vcodec") == "none"
            and f.get("acodec") not in (None, "none") and not f.get("has_drm")
        ]
        print(f"Selected Instagram format: quality={quality}p, codec={video.get('vcodec')}, id={video['format_id']}")
        if video.get("acodec") not in (None, "none") or not audio_formats:
            yield video
            return
        # yt-dlp supplies formats in ascending preference order.
        audio = next((f for f in reversed(audio_formats) if f.get("ext") == "m4a"), audio_formats[-1])
        yield {
            "format_id": f"{video['format_id']}+{audio['format_id']}",
            "ext": video["ext"], "requested_formats": [video, audio],
            "protocol": f"{video['protocol']}+{audio['protocol']}",
            "vcodec": video.get("vcodec"), "acodec": audio.get("acodec"),
            "width": video.get("width"), "height": video.get("height"), "fps": video.get("fps"),
        }
    return select_format


def _get_format_selector(
    source_url: str,
    format_id: str | None,
    quality: str | None,
    media_type: str | None,
    playlist_index: int | None = None,
) -> str | Callable:

    if _is_instagram_url(source_url) and not format_id and media_type != "audio":
        requested = _get_quality_height(quality)
        if requested is not None:
            return _instagram_format_selector(requested)

    # ========================================================
    # Audio
    # ========================================================

    if (
        media_type
        == "audio"
    ):

        if format_id:

            return (
                format_id
            )

        if _is_youtube_url(
            source_url
        ):

            return (
                "bestaudio"
                "[ext=m4a]"
                "[format_id!*=drc]"
                "/"
                "bestaudio"
                "[ext=m4a]"
                "/"
                "bestaudio"
            )

        return (
            "bestaudio/best"
        )

    # ========================================================
    # Explicit format
    # ========================================================

    if format_id:

        if "+" in format_id:

            return (
                format_id
            )

        ydl_options = (
            _get_yt_dlp_options(
                source_url,
                quiet=True,
                no_warnings=True,
                skip_download=True,
                noplaylist=True,
            )
        )

        with yt_dlp.YoutubeDL(
            ydl_options
        ) as ydl:

            info = ydl.extract_info(
                source_url,
                download=False,
            )

        formats = info.get(
            "formats",
            [],
        )

        selected_format = None

        for item in formats:

            if str(
                item.get(
                    "format_id"
                )
            ) == str(
                format_id
            ):

                selected_format = (
                    item
                )

                break

        if (
            selected_format
            is None
        ):

            raise ValueError(
                "Requested format_id "
                f"'{format_id}' "
                "was not found"
            )

        has_video = (
            selected_format.get(
                "vcodec"
            )
            not in (
                None,
                "none",
            )
        )

        has_audio = (
            selected_format.get(
                "acodec"
            )
            not in (
                None,
                "none",
            )
        )

        if (
            has_video
            and has_audio
        ):

            return (
                format_id
            )

        if (
            has_video
            and not has_audio
        ):

            return (
                f"{format_id}"
                "+"
                "bestaudio"
                "[ext=m4a]"
                "[format_id!*=drc]"
                "/"
                f"{format_id}"
                "+"
                "bestaudio"
            )

        if (
            has_audio
            and not has_video
        ):

            return (
                format_id
            )

        raise ValueError(
            "Requested format_id "
            f"'{format_id}' "
            "is not usable"
        )

    # ========================================================
    # Quality
    # ========================================================

    requested_quality = (
        _get_quality_height(
            quality
        )
    )

    if (
        requested_quality
        is not None
    ):

        # ====================================================
        # YouTube
        # ====================================================

        if _is_youtube_url(
            source_url
        ):

            h264_selector = (
                "bestvideo"
                f"[height={requested_quality}]"
                "[ext=mp4]"
                "[vcodec^=avc1]"
                "+"
                "bestaudio"
                "[ext=m4a]"
                "[format_id!*=drc]"
            )

            mp4_selector = (
                "bestvideo"
                f"[height={requested_quality}]"
                "[ext=mp4]"
                "+"
                "bestaudio"
                "[ext=m4a]"
                "[format_id!*=drc]"
            )

            h264_generic_audio = (
                "bestvideo"
                f"[height={requested_quality}]"
                "[ext=mp4]"
                "[vcodec^=avc1]"
                "+"
                "bestaudio"
            )

            generic_selector = (
                "bestvideo"
                f"[height={requested_quality}]"
                "+"
                "bestaudio"
            )

            progressive_selector = (
                "best"
                f"[height={requested_quality}]"
            )

            return (
                f"{h264_selector}"
                f"/{mp4_selector}"
                f"/{h264_generic_audio}"
                f"/{generic_selector}"
                f"/{progressive_selector}"
            )

        # ====================================================
        # Instagram / TikTok / X / Facebook / ...
        # ====================================================

        return (
            _get_non_youtube_format_selector(
                source_url,
                requested_quality,
                playlist_index=(
                    playlist_index
                ),
            )
        )

    # ========================================================
    # Default
    # ========================================================

    if _is_youtube_url(
        source_url
    ):

        return (
            "bestvideo"
            "[ext=mp4]"
            "[vcodec^=avc1]"
            "+"
            "bestaudio"
            "[ext=m4a]"
            "[format_id!*=drc]"
            "/"
            "bestvideo"
            "+"
            "bestaudio"
            "/"
            "best"
        )

    return (
        "bestvideo"
        "[ext=mp4]"
        "+"
        "bestaudio"
        "[ext=m4a]"
        "/"
        "bestvideo"
        "+"
        "bestaudio"
        "/"
        "best"
    )


# ============================================================
# Output quality validation
# ============================================================

def _validate_output_quality(
    source_url: str,
    quality: str | None,
    media_type: str | None,
    file_path: Path,
) -> None:

    if (
        media_type
        != "video"
    ):

        return

    requested_quality = (
        _get_quality_height(
            quality
        )
    )

    if (
        requested_quality
        is None
    ):

        return

    resolution = (
        _get_video_resolution(
            file_path
        )
    )

    if (
        resolution
        is None
    ):

        raise RuntimeError(
            "Unable to validate "
            "downloaded video resolution"
        )

    width, height = (
        resolution
    )

    # --------------------------------------------------------
    # YouTube
    # --------------------------------------------------------

    if _is_youtube_url(
        source_url
    ):

        actual_quality = (
            height
        )

    # --------------------------------------------------------
    # Other platforms
    #
    # Portrait:
    # 720x1280 -> 720p
    #
    # Landscape:
    # 1280x720 -> 720p
    # --------------------------------------------------------

    else:

        actual_quality = min(
            width,
            height,
        )

    print(
        "Quality validation: "
        f"requested="
        f"{requested_quality}p, "
        f"actual="
        f"{width}x{height}, "
        f"normalized="
        f"{actual_quality}p"
    )

    # ========================================================
    # Resolution tolerance
    #
    # Non-YouTube platforms often expose unusual dimensions:
    #
    # 364x640 -> shown to user as 360p
    # 718x1280 -> can reasonably represent 720p
    #
    # YouTube remains strict because its quality labels map
    # directly to standard stream heights.
    # ========================================================

    if _is_youtube_url(
        source_url
    ):

        quality_matches = (
            actual_quality
            == requested_quality
        )

    else:

        tolerance = max(
            8,
            int(
                requested_quality
                * 0.03
            ),
        )

        quality_matches = (
            abs(
                actual_quality
                - requested_quality
            )
            <= tolerance
        )

    if not quality_matches:

        raise RuntimeError(
            f"Requested quality "
            f"{requested_quality}p "
            f"but downloaded video is "
            f"{width}x{height} "
            f"({actual_quality}p)"
        )


# ============================================================
# Paused file cleanup
# ============================================================

@celery_app.task(
    name="mediahub.cleanup_paused_download",
)
def cleanup_paused_download(
    job_id: int,
) -> str:

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:

            return (
                f"Job {job_id} not found"
            )

        if (
            job.status
            != DownloadJobStatus.PAUSED
        ):

            return (
                f"Job {job_id} "
                "is no longer paused"
            )

        if (
            job.paused_at
            is None
        ):

            return (
                f"Job {job_id} "
                "has no paused_at"
            )

        now = (
            datetime.now(
                timezone.utc
            )
        )

        expires_at = (
            job.paused_at
            + timedelta(
                hours=(
                    PAUSED_FILE_TTL_HOURS
                )
            )
        )

        if (
            now
            < expires_at
        ):

            return (
                f"Job {job_id} "
                "has not expired yet"
            )

    _cleanup_uploaded_source_reference(job.source_url)
    _cleanup_job_files(
        job_id
    )

    _set_expired(
        job_id
    )

    print(
        "Paused download expired: "
        f"job={job_id}"
    )

    return (
        f"Job {job_id} expired"
    )


# ============================================================
# Main download task
# ============================================================

@celery_app.task(
    name="mediahub.download",
    bind=True,
    max_retries=3,
)
def download_task(
    self,
    job_id: int,
) -> str:

    # ========================================================
    # Load job
    # ========================================================

    with WorkerSessionLocal() as session:

        job = session.execute(
            select(
                DownloadJob
            ).where(
                DownloadJob.id
                == job_id
            )
        ).scalar_one_or_none()

        if job is None:

            return (
                f"Download job "
                f"{job_id} "
                "not found"
            )

        if (
            job.status
            == DownloadJobStatus.PAUSED
        ):

            return (
                f"Download job "
                f"{job_id} paused"
            )

        if (
            job.status
            == DownloadJobStatus.CANCELLED
        ):

            _cleanup_uploaded_source_reference(job.source_url)

            _cleanup_job_files(
                job_id
            )

            return (
                f"Download job "
                f"{job_id} cancelled"
            )

        if (
            job.status
            == DownloadJobStatus.EXPIRED
        ):

            _cleanup_uploaded_source_reference(job.source_url)

            _cleanup_job_files(
                job_id
            )

            return (
                f"Download job "
                f"{job_id} expired"
            )

        if (
            job.status
            == DownloadJobStatus.COMPLETED
        ):

            return (
                f"Download job "
                f"{job_id} already completed"
            )

        source_url = (
            job.source_url
        )

        format_id = (
            job.format_id
        )

        quality = (
            job.quality
        )

        media_type = (
            job.media_type
        )

        output_format = (
            job.output_format
        )

        playlist_index = (
            job.playlist_index
        )

        max_download_bytes = _resolve_max_download_bytes(
            job.plan_limits_snapshot
        )

    # ========================================================
    # Processing
    # ========================================================

    if not _set_processing(
        job_id
    ):

        return (
            f"Download job "
            f"{job_id} cannot start"
        )

    DOWNLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Upload-based conversion jobs keep the staged source outside the
    # job-id glob until conversion succeeds.  Keep a reference so every
    # terminal path can remove it without ever touching an arbitrary path.
    uploaded_source: Path | None = None

    # --------------------------------------------------------
    # IMPORTANT:
    # Do not delete existing .part files.
    # Resume depends on them.
    # --------------------------------------------------------

    last_progress = -1

    last_downloaded_bytes = -1

    last_total_bytes = -1

    last_speed: (
        float
        | None
    ) = None

    last_eta: (
        int
        | None
    ) = None

    # ========================================================
    # Progress hook
    # ========================================================

    download_part_stats: dict[
        str,
        dict[
            str,
            int | None,
        ],
    ] = {}

    def progress_hook(
        data: dict,
    ) -> None:

        nonlocal last_progress

        nonlocal last_downloaded_bytes

        nonlocal last_total_bytes

        nonlocal last_speed

        nonlocal last_eta

        _check_job_control(
            job_id
        )

        if (
            data.get(
                "status"
            )
            != "downloading"
        ):

            return

        # ----------------------------------------------------
        # Downloaded bytes
        # --------------------------------------------------------

        downloaded_bytes = (
            data.get(
                "downloaded_bytes"
            )
        )

        if (
            downloaded_bytes
            is None
        ):

            downloaded_bytes = 0

        try:

            downloaded_bytes = int(
                downloaded_bytes
            )

        except (
            TypeError,
            ValueError,
        ):

            downloaded_bytes = 0

        # ----------------------------------------------------
        # Total
        # --------------------------------------------------------

        total_bytes = (
            data.get(
                "total_bytes"
            )
            or data.get(
                "total_bytes_estimate"
            )
        )

        if (
            total_bytes
            is not None
        ):

            try:

                total_bytes = int(
                    total_bytes
                )

            except (
                TypeError,
                ValueError,
            ):

                total_bytes = None

        # ----------------------------------------------------
        # Aggregate separate video/audio downloads.
        #
        # yt-dlp downloads DASH video and audio as separate
        # files. Without aggregation the second (small audio)
        # stream overwrites DB stats from the main video and
        # users see values such as 358 KB / 358 KB.
        # --------------------------------------------------------

        info_dict = (
            data.get(
                "info_dict"
            )
        )

        if not isinstance(
            info_dict,
            dict,
        ):

            info_dict = {}

        part_key = (
            f"{data.get('filename') or data.get('tmpfilename') or 'download'}"
            "|"
            f"{info_dict.get('format_id') or ''}"
        )

        download_part_stats[
            part_key
        ] = {
            "downloaded":
                downloaded_bytes,

            "total":
                total_bytes,
        }

        downloaded_bytes = sum(
            int(
                part.get(
                    "downloaded"
                )
                or 0
            )
            for part
            in download_part_stats.values()
        )

        if downloaded_bytes > max_download_bytes:
            raise RuntimeError(
                "Downloaded data exceeds the plan file-size limit"
            )

        part_totals = [
            part.get(
                "total"
            )
            for part
            in download_part_stats.values()
        ]

        if (
            part_totals
            and all(
                isinstance(
                    value,
                    int,
                )
                and value > 0
                for value
                in part_totals
            )
        ):

            total_bytes = sum(
                int(
                    value
                )
                for value
                in part_totals
                if value is not None
            )

            if total_bytes > max_download_bytes:
                raise RuntimeError(
                    "Estimated output exceeds the plan file-size limit"
                )

        else:

            total_bytes = None

        # ----------------------------------------------------
        # Speed
        # --------------------------------------------------------

        speed = (
            data.get(
                "speed"
            )
        )

        if (
            speed
            is not None
        ):

            try:

                speed = float(
                    speed
                )

            except (
                TypeError,
                ValueError,
            ):

                speed = None

        # ----------------------------------------------------
        # ETA
        # --------------------------------------------------------

        eta = (
            data.get(
                "eta"
            )
        )

        if (
            eta
            is not None
        ):

            try:

                eta = int(
                    eta
                )

            except (
                TypeError,
                ValueError,
            ):

                eta = None

        # ----------------------------------------------------
        # ETA fallback
        #
        # Some extractors report eta=0/None. Calculate it from
        # remaining bytes and current speed when possible.
        # --------------------------------------------------------

        if (
            eta is not None
            and eta <= 0
        ):

            eta = None

        if (
            eta is None
            and total_bytes
            is not None
            and total_bytes > 0
            and downloaded_bytes
            < total_bytes
            and speed
            is not None
            and speed > 0
        ):

            remaining_bytes = (
                total_bytes
                - downloaded_bytes
            )

            eta = max(
                1,
                int(
                    remaining_bytes
                    / speed
                ),
            )

        # ----------------------------------------------------
        # Progress
        # --------------------------------------------------------

        progress = 0

        if (
            total_bytes
            and total_bytes > 0
        ):

            progress = int(
                (
                    downloaded_bytes
                    / total_bytes
                )
                * 100
            )

        progress = max(
            0,
            min(
                progress,
                99,
            ),
        )

        # ----------------------------------------------------
        # Reduce DB writes
        # --------------------------------------------------------

        downloaded_delta = abs(
            downloaded_bytes
            - last_downloaded_bytes
        )

        normalized_total = (
            total_bytes
            if total_bytes is not None
            else -1
        )

        speed_changed = False

        if (
            speed
            is not None
        ):

            if (
                last_speed is None
                or abs(
                    speed
                    - last_speed
                )
                >= 128 * 1024
            ):

                speed_changed = True

        should_update = (
            progress
            != last_progress

            or downloaded_delta
            >= 1024 * 1024

            or normalized_total
            != last_total_bytes

            or speed_changed

            or eta
            != last_eta
        )

        if not should_update:
            return

        last_progress = (
            progress
        )

        last_downloaded_bytes = (
            downloaded_bytes
        )

        last_total_bytes = (
            normalized_total
        )

        last_speed = (
            speed
        )

        last_eta = (
            eta
        )

        _update_download_stats(
            job_id=job_id,
            progress=progress,
            downloaded_bytes=(
                downloaded_bytes
            ),
            total_bytes=(
                total_bytes
            ),
            speed=speed,
            eta=eta,
        )

    # ========================================================
    # Output
    # ========================================================

    output_template = str(
        DOWNLOAD_DIR
        / f"{job_id}.%(ext)s"
    )

    try:

        _check_job_control(
            job_id
        )

        # ====================================================
        # Image
        # ====================================================

        if (
            media_type
            == "image"
        ):

            print(
                "Starting image download job "
                f"{job_id}: "
                f"playlist_index="
                f"{playlist_index}"
            )

            file_path = (
                _download_social_image(
                    job_id=job_id,
                    source_url=source_url,
                    playlist_index=(
                        playlist_index
                    ),
                    max_download_bytes=max_download_bytes,
                )
            )

        # ====================================================
        # Uploaded conversion
        # ====================================================

        elif media_type == "convert" and str(source_url).startswith(UPLOAD_SOURCE_PREFIX):
            uploaded_source = _resolve_uploaded_source(source_url)
            file_path = _run_ffmpeg_conversion(
                job_id=job_id,
                input_path=uploaded_source,
                output_format=str(output_format or ""),
                max_download_bytes=max_download_bytes,
            )
            _cleanup_uploaded_source(uploaded_source)
            uploaded_source = None

        # ====================================================
        # Audio / Video / URL conversion
        # ====================================================

        else:

            target_kind = output_format_kind(output_format) if output_format else None
            download_media_type = (
                target_kind
                if media_type == "convert" and target_kind in {"audio", "video"}
                else media_type
            )
            if media_type == "convert" and target_kind is None:
                raise RuntimeError("A valid conversion output format is required")

            direct_download_url = source_url
            threads_direct_video = (
                download_media_type == "video"
                and is_threads_url(source_url)
            )

            if threads_direct_video:

                threads_video = extract_threads_video(
                    source_url=source_url,
                    playlist_index=playlist_index,
                )
                direct_download_url = str(threads_video["url"])
                format_selector = "best"

            else:

                format_selector = (
                    _get_format_selector(
                        source_url=source_url,
                        format_id=format_id,
                        quality=quality,
                        media_type=download_media_type,
                        playlist_index=(
                            playlist_index
                        ),
                    )
                )

            common_options = {
                "outtmpl":
                    output_template,

                "format":
                    format_selector,

                "progress_hooks": [
                    progress_hook,
                ],

                "noplaylist":
                    True,

                "quiet":
                    True,

                "no_warnings":
                    True,

                "continuedl":
                    True,

                "nopart":
                    False,
            }

            if threads_direct_video:

                common_options[
                    "http_headers"
                ] = {
                    "User-Agent": BROWSER_USER_AGENT,
                    "Referer": social_referer("threads"),
                }

            # ================================================
            # Playlist item
            # ================================================

            if (
                playlist_index
                is not None
                and not threads_direct_video
            ):

                common_options[
                    "playlist_items"
                ] = str(
                    playlist_index
                )

                common_options[
                    "noplaylist"
                ] = False

            # ================================================
            # Audio
            # ================================================

            if download_media_type == "audio":

                ydl_options = (
                    _get_yt_dlp_options(
                        source_url,
                        **common_options,
                    )
                )

            # ================================================
            # Video
            # ================================================

            else:

                ydl_options = (
                    _get_yt_dlp_options(
                        source_url,
                        merge_output_format="mp4",
                        **common_options,
                    )
                )

            print(
                "Starting download job "
                f"{job_id}: "
                f"quality={quality}, "
                f"format_id={format_id}, "
                f"media_type={media_type}, "
                f"playlist_index={playlist_index}, "
                f"threads_direct={threads_direct_video}, "
                f"youtube="
                f"{_is_youtube_url(source_url)}, "
                f"selector="
                f"{format_selector if isinstance(format_selector, str) else 'instagram-auto'}"
            )

            download_started = time.monotonic()
            with yt_dlp.YoutubeDL(
                ydl_options
            ) as ydl:

                ydl.download(
                    [
                        direct_download_url,
                    ]
                )
            print(f"Media download complete: job={job_id}, elapsed_s={time.monotonic()-download_started:.2f}")

            _check_job_control(
                job_id
            )

            file_path = _find_final_output_file(
                job_id=job_id,
                media_type=download_media_type,
            )

            file_path = _prepare_requested_output(
                job_id=job_id, file_path=file_path, media_type=media_type,
                output_format=output_format, max_download_bytes=max_download_bytes,
            )

            _check_job_control(
                job_id
            )

        # ====================================================
        # Final file checks
        # ====================================================

        file_size = (
            file_path
            .stat()
            .st_size
        )

        if file_size <= 0:

            raise RuntimeError(
                "Downloaded file is empty"
            )

        if (
            file_size
            > max_download_bytes
        ):

            raise RuntimeError(
                "Downloaded file exceeds "
                "the plan file-size limit"
            )

        print(
            "Selected final output: "
            f"{file_path}, "
            f"size={file_size}"
        )

        # ====================================================
        # Validate video/audio quality
        #
        # Images have no video quality to validate.
        # ====================================================

        if (
            media_type
            != "image"
        ):

            _validate_output_quality(
                source_url=source_url,
                quality=quality,
                media_type=media_type,
                file_path=file_path,
            )

        _check_job_control(
            job_id
        )

        # ====================================================
        # Completed
        # ====================================================

        _set_completed(
            job_id=job_id,
            file_path=str(
                file_path
            ),
            file_size=file_size,
        )

        print(
            "Download job "
            f"{job_id} completed: "
            f"{file_path} "
            f"({file_size} bytes)"
        )

        return (
            f"Download job "
            f"{job_id} completed"
        )

    # ========================================================
    # Pause
    # ========================================================

    except DownloadPausedError:
        print(
            "Download job paused: "
            f"{job_id}"
        )

        return (
            f"Download job "
            f"{job_id} paused"
        )

    # ========================================================
    # Cancel / expire
    # ========================================================

    except DownloadCancelledError:

        _cleanup_uploaded_source(uploaded_source)

        status = (
            _get_job_status(
                job_id
            )
        )

        print(
            "Download job stopped: "
            f"{job_id}, "
            f"status={status}"
        )

        _cleanup_job_files(
            job_id
        )

        return (
            f"Download job "
            f"{job_id} stopped"
        )

    # ========================================================
    # Other exceptions
    # ========================================================

    except Exception as exc:

        error_message = (
            str(
                exc
            )
        )

        current_status = (
            _get_job_status(
                job_id
            )
        )

        # ----------------------------------------------------
        # yt-dlp can wrap progress-hook exceptions.
        # ----------------------------------------------------

        if (
            current_status
            == DownloadJobStatus.PAUSED
        ):

            print(
                "Download job paused "
                "inside yt-dlp exception: "
                f"{job_id}"
            )

            return (
                f"Download job "
                f"{job_id} paused"
            )

        if current_status in (
            DownloadJobStatus.CANCELLED,
            DownloadJobStatus.EXPIRED,
        ):

            _cleanup_uploaded_source(uploaded_source)

            _cleanup_job_files(
                job_id
            )

            return (
                f"Download job "
                f"{job_id} stopped"
            )

        print(
            "Download job "
            f"{job_id} failed: "
            f"{error_message}"
        )

        # ====================================================
        # Retry
        # ====================================================

        if (
            self.request.retries
            < self.max_retries
        ):
            countdown = min(
                60,
                2 ** (
                    self.request.retries
                    + 1
                ),
            )

            raise self.retry(
                exc=exc,
                countdown=countdown,
            )

        # ====================================================
        # Final failure
        # ====================================================

        _cleanup_job_files(
            job_id
        )

        _cleanup_uploaded_source(uploaded_source)

        _set_failed(
            job_id=job_id,
            error_message=(
                error_message
            ),
        )

        raise
