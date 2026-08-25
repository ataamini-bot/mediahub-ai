from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
from typing import Any
import json
import os
import subprocess

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
        # Select format
        # ====================================================

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

        # ====================================================
        # X / Twitter multi-video playlist item
        # ====================================================

        if (
            playlist_index
            is not None
        ):

            common_options[
                "playlist_items"
            ] = str(
                playlist_index
            )

            # The selected item must be allowed through.
            common_options[
                "noplaylist"
            ] = False

        # ====================================================
        # Audio
        # ====================================================

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

        # ====================================================
        # Video
        # ====================================================

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

        # ====================================================
        # Download
        # ====================================================

        with yt_dlp.YoutubeDL(
            ydl_options
        ) as ydl:

            ydl.download(
                [
                    source_url,
                ]
            )

        _check_job_control(
            job_id
        )

        # ====================================================
        # Find the actual final media file
        # ====================================================

        file_path = (
            _find_final_output_file(
                job_id=job_id,
                media_type=media_type,
            )
        )

        file_size = (
            file_path
            .stat()
            .st_size
        )

        if (
            file_size <= 0
        ):

            raise RuntimeError(
                "Downloaded file is empty"
            )

        print(
            "Selected final output: "
            f"{file_path}, "
            f"size="
            f"{file_size}"
        )

        # ====================================================
        # Validate quality
        # ====================================================

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
