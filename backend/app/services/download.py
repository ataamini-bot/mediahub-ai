from datetime import datetime, timezone
from typing import Any
import ipaddress
import os
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

def _get_format_filesize(
    item: dict[str, Any],
) -> int | None:

    filesize = (
        _positive_int(
            item.get(
                "filesize"
            )
        )
    )

    if filesize is not None:

        return filesize

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

        return (
            filesize_approx
        )

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
            item
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
                item
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
        user_id: int | None = None,
        format_id: str | None = None,
        quality: str | None = None,
        media_type: str | None = None,
        playlist_index: int | None = None,
    ) -> DownloadJob:

        # ----------------------------------------------------
        # Keep repository backward-compatible.
        #
        # The current repository does not yet need to know
        # about playlist_index; we save it after creation.
        # ----------------------------------------------------

        job = (
            await self.repository.create(
                source_url=(
                    source_url
                ),
                user_id=(
                    user_id
                ),
                format_id=(
                    format_id
                ),
                quality=(
                    quality
                ),
                media_type=(
                    media_type
                ),
            )
        )

        if (
            playlist_index
            is not None
        ):

            job.playlist_index = (
                playlist_index
            )

            await (
                self.session.commit()
            )

            await (
                self.session.refresh(
                    job
                )
            )

        # ----------------------------------------------------
        # Queue Celery task
        # ----------------------------------------------------

        task = (
            download_task.delay(
                job.id
            )
        )

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

        task = (
            download_task.delay(
                job.id
            )
        )

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

                entry_formats = (
                    _normalize_formats(
                        raw_entry.get(
                            "formats"
                        )
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
                                else index
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
                            _normalize_duration(
                                raw_entry.get(
                                    "duration"
                                )
                            ),

                        "thumbnail":
                            raw_entry.get(
                                "thumbnail"
                            ),

                        "formats":
                            entry_formats,
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
                playlist_index
                is not None
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

        formats = (
            _normalize_formats(
                info.get(
                    "formats"
                )
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
                _normalize_duration(
                    info.get(
                        "duration"
                    )
                ),

            "thumbnail":
                info.get(
                    "thumbnail"
                ),

            "formats":
                formats,

            "is_playlist":
                False,

            "entry_count":
                0,

            "entries":
                [],
        }
