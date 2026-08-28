from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import json
import os
import tempfile
import subprocess
import requests

import yt_dlp
from sqlalchemy import select

from app.db.worker_session import WorkerSessionLocal
from app.models.download_job import (
    DownloadJob,
    DownloadJobStatus,
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

INSTAGRAM_COOKIE_PATH = Path(
    os.getenv(
        "INSTAGRAM_COOKIE_PATH",
        "/app/secrets/instagram-cookies.txt",
    )
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


def _prepare_instagram_yt_dlp_cookie_file(
) -> str:

    source_path = os.fspath(
        INSTAGRAM_COOKIE_PATH
    )

    if not os.path.isfile(
        source_path
    ):

        raise RuntimeError(
            "Instagram cookie file not found: "
            f"{source_path}"
        )

    temporary_path: str | None = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="mediahub-instagram-",
            suffix=".cookies.txt",
            dir="/tmp",
            delete=False,
        ) as temporary_file:

            temporary_path = (
                temporary_file.name
            )

            os.chmod(
                temporary_path,
                0o600,
            )

            with open(
                source_path,
                "rb",
            ) as source_file:

                while True:

                    chunk = (
                        source_file.read(
                            1024 * 1024
                        )
                    )

                    if not chunk:
                        break

                    temporary_file.write(
                        chunk
                    )

        return temporary_path

    except Exception:

        if temporary_path:

            try:

                os.remove(
                    temporary_path
                )

            except FileNotFoundError:

                pass

        raise


def _cleanup_instagram_yt_dlp_cookie_file(
    cookie_path: str | None,
) -> None:

    if not cookie_path:
        return

    try:

        os.remove(
            cookie_path
        )

    except FileNotFoundError:

        pass

    except OSError as exc:

        print(
            "Failed to remove temporary "
            "Instagram cookie file: "
            f"{type(exc).__name__}: "
            f"{exc}"
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

    if not INSTAGRAM_COOKIE_PATH.is_file():

        raise RuntimeError(
            "Instagram cookie file not found: "
            f"{INSTAGRAM_COOKIE_PATH}"
        )

    command = [
        "gallery-dl",
        "--cookies",
        str(
            INSTAGRAM_COOKIE_PATH
        ),
        "-j",
        source_url,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    if result.returncode != 0:

        raise RuntimeError(
            "gallery-dl failed while extracting "
            "Instagram image: "
            f"{result.stderr[-1000:]}"
        )

    try:

        payload = json.loads(
            result.stdout
        )

    except Exception as exc:

        raise RuntimeError(
            "Invalid gallery-dl JSON output"
        ) from exc

    if not isinstance(
        payload,
        list,
    ):

        raise RuntimeError(
            "Unexpected gallery-dl output"
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

        if not isinstance(
            raw_url,
            str,
        ):

            continue

        if not isinstance(
            metadata,
            dict,
        ):

            continue

        extension = (
            _normalize_image_extension(
                metadata.get(
                    "extension"
                )
            )
        )

        if extension is None:

            continue

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

        if not _is_allowed_instagram_cdn_url(
            raw_url
        ):

            raise RuntimeError(
                "Extractor returned an unexpected "
                "Instagram image host"
            )

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

    # Single-photo Instagram post.
    if len(
        image_entries
    ) == 1:

        return image_entries[0]

    raise ValueError(
        "playlist_index is required for an "
        "Instagram post containing multiple images"
    )


def _download_social_image(
    job_id: int,
    source_url: str,
    playlist_index: int | None,
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

    else:

        raise ValueError(
            "Image downloads currently support "
            "Instagram and X URLs"
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
            > MAX_DOWNLOAD_BYTES
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
        > MAX_DOWNLOAD_BYTES
    ):

        response.close()

        raise RuntimeError(
            "Image exceeds the 1900 MB "
            "download limit"
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
                    > MAX_DOWNLOAD_BYTES
                ):

                    raise RuntimeError(
                        "Image exceeds the 1900 MB "
                        "download limit"
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

    # --------------------------------------------------------
    # Instagram Story authentication
    # --------------------------------------------------------

    if _is_instagram_story_url(
        source_url
    ):

        ydl_options[
            "cookiefile"
        ] = (
            _prepare_instagram_yt_dlp_cookie_file()
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
# iOS / Instagram video compatibility
# ============================================================

def _probe_video_codec_for_ios(
    file_path: Path,
) -> tuple[
    str | None,
    str | None,
]:

    try:

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,pix_fmt",
                "-of",
                "csv=p=0:s=|",
                str(
                    file_path
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

    except (
        FileNotFoundError,
        subprocess.SubprocessError,
        OSError,
    ):

        return (
            None,
            None,
        )

    value = (
        result.stdout
        .strip()
    )

    if not value:

        return (
            None,
            None,
        )

    parts = [
        part
        .strip()
        .lower()
        for part
        in value.split(
            "|"
        )
    ]

    codec_name = (
        parts[0]
        if parts
        and parts[0]
        else None
    )

    pixel_format = (
        parts[1]
        if len(parts) > 1
        and parts[1]
        else None
    )

    return (
        codec_name,
        pixel_format,
    )


def _normalize_instagram_video_for_ios(
    job_id: int,
    file_path: Path,
) -> Path:

    codec_name, pixel_format = (
        _probe_video_codec_for_ios(
            file_path
        )
    )

    is_compatible = (
        file_path.suffix.lower()
        == ".mp4"
        and codec_name
        == "h264"
        and pixel_format
        == "yuv420p"
    )

    print(
        "Instagram iOS compatibility check: "
        f"job={job_id}, "
        f"file={file_path}, "
        f"codec={codec_name}, "
        f"pix_fmt={pixel_format}, "
        f"compatible={is_compatible}"
    )

    # --------------------------------------------------------
    # Already compatible:
    #
    # H.264 + yuv420p inside MP4.
    # Do not waste CPU by transcoding it again.
    # --------------------------------------------------------

    if is_compatible:

        return file_path

    # --------------------------------------------------------
    # Do not waste time transcoding an output that will be
    # rejected by the global size check anyway.
    # --------------------------------------------------------

    try:

        source_size = (
            file_path.stat()
            .st_size
        )

    except OSError as exc:

        raise RuntimeError(
            "Unable to inspect video "
            "before iOS normalization"
        ) from exc

    if (
        source_size <= 0
        or source_size
        > MAX_DOWNLOAD_BYTES
    ):

        return file_path

    # --------------------------------------------------------
    # .part is intentional:
    #
    # _find_final_output_file() ignores .part files, so a
    # failed/interrupted normalization cannot later be selected
    # as a completed download.
    # --------------------------------------------------------

    temporary_path = (
        DOWNLOAD_DIR
        / (
            f"{job_id}"
            ".ios-normalized.mp4.part"
        )
    )

    final_path = (
        DOWNLOAD_DIR
        / f"{job_id}.mp4"
    )

    try:

        temporary_path.unlink()

    except FileNotFoundError:

        pass

    except OSError as exc:

        raise RuntimeError(
            "Unable to prepare temporary "
            "iOS video output"
        ) from exc

    print(
        "Normalizing Instagram video "
        "for iOS compatibility: "
        f"job={job_id}, "
        f"codec={codec_name}, "
        f"pix_fmt={pixel_format}"
    )

    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",

        "-i",
        str(
            file_path
        ),

        "-map",
        "0:v:0",

        "-map",
        "0:a:0?",

        "-c:v",
        "libx264",

        "-preset",
        "fast",

        "-crf",
        "20",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-profile:a",
        "aac_low",

        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        "-f",
        "mp4",

        str(
            temporary_path
        ),
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )

    except FileNotFoundError as exc:

        raise RuntimeError(
            "FFmpeg is not available "
            "for iOS video normalization"
        ) from exc

    except OSError as exc:

        raise RuntimeError(
            "Unable to start FFmpeg "
            "for iOS video normalization"
        ) from exc

    if (
        result.returncode
        != 0
    ):

        try:

            temporary_path.unlink()

        except FileNotFoundError:

            pass

        error_text = (
            result.stderr
            .strip()
        )

        if len(
            error_text
        ) > 2000:

            error_text = (
                error_text[
                    -2000:
                ]
            )

        raise RuntimeError(
            "iOS video normalization "
            "failed"
            + (
                f": {error_text}"
                if error_text
                else ""
            )
        )

    if (
        not temporary_path.exists()
    ):

        raise RuntimeError(
            "iOS video normalization "
            "did not create an output file"
        )

    if (
        temporary_path.stat()
        .st_size
        <= 0
    ):

        try:

            temporary_path.unlink()

        except FileNotFoundError:

            pass

        raise RuntimeError(
            "iOS normalized video is empty"
        )

    normalized_codec, normalized_pix_fmt = (
        _probe_video_codec_for_ios(
            temporary_path
        )
    )

    if (
        normalized_codec
        != "h264"
        or normalized_pix_fmt
        != "yuv420p"
    ):

        try:

            temporary_path.unlink()

        except FileNotFoundError:

            pass

        raise RuntimeError(
            "iOS normalized video "
            "failed codec validation: "
            f"codec={normalized_codec}, "
            f"pix_fmt={normalized_pix_fmt}"
        )

    # --------------------------------------------------------
    # Atomic replacement.
    #
    # The original file stays untouched until FFmpeg has
    # produced and validated the complete replacement.
    # --------------------------------------------------------

    original_path = (
        file_path
    )

    try:

        temporary_path.replace(
            final_path
        )

    except OSError as exc:

        try:

            temporary_path.unlink()

        except FileNotFoundError:

            pass

        raise RuntimeError(
            "Unable to install "
            "iOS-compatible video"
        ) from exc

    if (
        original_path
        != final_path
        and original_path.exists()
    ):

        try:

            original_path.unlink()

        except OSError as exc:

            print(
                "Unable to delete original "
                "incompatible video: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    print(
        "Instagram video normalized "
        "for iOS: "
        f"job={job_id}, "
        f"file={final_path}, "
        "codec=h264, "
        "pix_fmt=yuv420p"
    )

    return final_path


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

    temporary_cookie = (
        ydl_options.get(
            "cookiefile"
        )
    )

    try:

        with yt_dlp.YoutubeDL(
            ydl_options
        ) as ydl:

            info = ydl.extract_info(
                source_url,
                download=False,
            )

    finally:

        _cleanup_instagram_yt_dlp_cookie_file(
            (
                str(
                    temporary_cookie
                )
                if temporary_cookie
                else None
            )
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

def _get_format_selector(
    source_url: str,
    format_id: str | None,
    quality: str | None,
    media_type: str | None,
    playlist_index: int | None = None,
) -> str:

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

        playlist_index = (
            job.playlist_index
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
                )
            )

        # ====================================================
        # Audio / Video
        # ====================================================

        else:

            format_selector = (
                _get_format_selector(
                    source_url=source_url,
                    format_id=format_id,
                    quality=quality,
                    media_type=media_type,
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

            # ================================================
            # Playlist item
            # ================================================

            if (
                playlist_index
                is not None
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

            if (
                media_type
                == "audio"
            ):

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
                f"youtube="
                f"{_is_youtube_url(source_url)}, "
                f"selector="
                f"{format_selector}"
            )

            temporary_cookie = (
                ydl_options.get(
                    "cookiefile"
                )
            )

            try:

                with yt_dlp.YoutubeDL(
                    ydl_options
                ) as ydl:

                    ydl.download(
                        [
                            source_url,
                        ]
                    )

            finally:

                _cleanup_instagram_yt_dlp_cookie_file(
                    (
                        str(
                            temporary_cookie
                        )
                        if temporary_cookie
                        else None
                    )
                )

            _check_job_control(
                job_id
            )

            file_path = (
                _find_final_output_file(
                    job_id=job_id,
                    media_type=media_type,
                )
            )

        # ====================================================
        # Instagram / iOS compatibility
        #
        # Some Instagram DASH outputs contain VP9 video inside
        # an MP4 container. Windows can decode those files, but
        # Telegram/iOS may play only the audio or freeze the
        # first video frame.
        #
        # Only incompatible Instagram video outputs are
        # normalized. Existing H.264/yuv420p MP4 files are
        # passed through unchanged.
        # ====================================================

        if (
            media_type
            == "video"
            and "instagram.com"
            in str(
                source_url
            ).lower()
        ):

            _check_job_control(
                job_id
            )

            file_path = (
                _normalize_instagram_video_for_ios(
                    job_id=job_id,
                    file_path=file_path,
                )
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
            > MAX_DOWNLOAD_BYTES
        ):

            raise RuntimeError(
                "Downloaded file exceeds "
                "the 1900 MB limit"
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

        _set_failed(
            job_id=job_id,
            error_message=(
                error_message
            ),
        )

        raise
