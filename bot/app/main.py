import asyncio
import html
import os
import re
import uuid
from pathlib import Path

import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_BOT_API = os.getenv(
    "TELEGRAM_BOT_API",
    "http://telegram-api:8081",
)

BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "http://backend:8000",
).rstrip("/")

DOWNLOAD_DIR = Path(
    os.getenv(
        "DOWNLOAD_DIR",
        "/app/downloads",
    )
)

POLL_INTERVAL = 2

MAX_WAIT_TIME = (
    7
    * 60
    * 60
)

MAX_DOWNLOAD_SIZE_MB = 1900

MAX_DOWNLOAD_SIZE_BYTES = (
    MAX_DOWNLOAD_SIZE_MB
    * 1024
    * 1024
)

dp = Dispatcher()


# ============================================================
# Pending selections
# ============================================================

PENDING_SELECTIONS: dict[
    str,
    dict,
] = {}

PENDING_MEDIA_ENTRIES: dict[
    str,
    dict,
] = {}


# ============================================================
# URL helpers
# ============================================================

def extract_url(
    text: str,
) -> str:

    text = (
        text.strip()
    )

    if not text:
        return ""

    markdown_urls = re.findall(
        r"\]\((https?://[^)\s]+)\)",
        text,
    )

    if markdown_urls:

        return (
            markdown_urls[-1]
        )

    raw_urls = re.findall(
        r"https?://[^\s<>\[\]()]+",
        text,
    )

    if raw_urls:

        return (
            raw_urls[-1]
        )

    return text


def is_youtube_url(
    source_url: str,
) -> bool:

    value = (
        source_url.lower()
    )

    hosts = (
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
    )

    return any(
        host in value
        for host in hosts
    )


# ============================================================
# Media title normalization
# ============================================================

def normalize_media_title(
    source_url: str,
    title: str | None,
) -> str:

    original_title = (
        str(
            title
            or ""
        )
        .strip()
    )

    normalized_title = (
        original_title
        .lower()
        .strip()
    )

    source_lower = (
        source_url
        .lower()
    )

    generic_titles = {
        "",
        "video",
        "instagram video",
        "instagram reel",
        "facebook video",
        "facebook reel",
        "tiktok video",
        "twitter video",
        "x video",
        "whatsapp video",
    }

    # --------------------------------------------------------
    # Detect generic titles returned by extractors
    #
    # Examples:
    # Video by facebook
    # Video by whatsapp
    # Video by instagram
    # Video by meta
    # --------------------------------------------------------

    is_generic_video_by = (
        normalized_title.startswith(
            "video by "
        )
    )

    # --------------------------------------------------------
    # Instagram
    # --------------------------------------------------------

    if (
        "instagram.com"
        in source_lower
    ):

        if (
            normalized_title
            in generic_titles
            or is_generic_video_by
        ):

            return (
                "Instagram Reel"
            )

        return (
            original_title
            or
            "Instagram Reel"
        )

    # --------------------------------------------------------
    # Facebook
    # --------------------------------------------------------

    if (
        "facebook.com"
        in source_lower
        or "fb.watch"
        in source_lower
    ):

        if (
            normalized_title
            in generic_titles
            or is_generic_video_by
        ):

            return (
                "Facebook Video"
            )

        return (
            original_title
            or
            "Facebook Video"
        )

    # --------------------------------------------------------
    # TikTok
    # --------------------------------------------------------

    if (
        "tiktok.com"
        in source_lower
    ):

        if (
            normalized_title
            in generic_titles
            or is_generic_video_by
        ):

            return (
                "TikTok Video"
            )

        return (
            original_title
            or
            "TikTok Video"
        )

    # --------------------------------------------------------
    # X / Twitter
    # --------------------------------------------------------

    if (
        "twitter.com"
        in source_lower
        or "x.com"
        in source_lower
    ):

        if (
            normalized_title
            in generic_titles
            or is_generic_video_by
        ):

            return (
                "X Video"
            )

        return (
            original_title
            or
            "X Video"
        )

    # --------------------------------------------------------
    # Pinterest
    # --------------------------------------------------------

    if (
        "pinterest.com"
        in source_lower
        or "pin.it"
        in source_lower
    ):

        if (
            normalized_title
            in generic_titles
            or is_generic_video_by
        ):

            return (
                "Pinterest Video"
            )

        return (
            original_title
            or
            "Pinterest Video"
        )

    # --------------------------------------------------------
    # Generic fallback
    # --------------------------------------------------------

    if (
        not original_title
        or normalized_title
        in generic_titles
        or is_generic_video_by
    ):

        return (
            "ویدئو"
        )

    return (
        original_title
    )

# ============================================================
# Quality helpers
# ============================================================

def normalize_quality_label(
    height: int,
) -> str:

    if (
        height >= 2160
    ):

        return "4K"

    if (
        height >= 1440
    ):

        return "2K"

    return (
        f"{height}p"
    )


def normalize_platform_quality(
    value: int,
) -> int:

    standards = (
        144,
        240,
        360,
        480,
        720,
        1080,
        1440,
        2160,
    )

    nearest = min(
        standards,
        key=lambda item: abs(
            item - value
        ),
    )

    tolerance = max(
        8,
        int(
            nearest
            * 0.03
        ),
    )

    if (
        abs(
            nearest
            - value
        )
        <= tolerance
    ):

        return nearest

    return value


def normalize_quality_options(
    quality_options: list[
        tuple[
            int,
            int | None,
        ]
    ],
) -> list[
    tuple[
        int,
        int | None,
    ]
]:

    result: dict[
        int,
        int | None,
    ] = {}

    for (
        quality,
        file_size,
    ) in quality_options:

        normalized = (
            normalize_platform_quality(
                quality
            )
        )

        current = (
            result.get(
                normalized
            )
        )

        if (
            current
            is None
        ):

            result[
                normalized
            ] = file_size

        elif (
            file_size
            is not None
            and file_size
            > current
        ):

            result[
                normalized
            ] = file_size

    return [
        (
            quality,
            result[
                quality
            ],
        )
        for quality
        in sorted(
            result
        )
    ]


def quality_sort_key(
    height: int,
) -> int:

    return height


# ============================================================
# File size helpers
# ============================================================

def format_file_size(
    file_size: (
        int
        | float
        | None
    ),
) -> str | None:

    if (
        file_size is None
        or file_size <= 0
    ):

        return None

    size = float(
        file_size
    )

    if (
        size < 1024
    ):

        return (
            f"{size:.0f} B"
        )

    kb = (
        size
        / 1024
    )

    if (
        kb < 1024
    ):

        if kb >= 100:
            return f"{kb:.0f} KB"

        if kb >= 10:
            return f"{kb:.0f} KB"

        return f"{kb:.1f} KB"

    mb = (
        kb
        / 1024
    )

    if (
        mb < 1024
    ):

        if mb >= 100:
            return f"{mb:.0f} MB"

        if mb >= 10:
            return f"{mb:.0f} MB"

        return f"{mb:.1f} MB"

    gb = (
        mb
        / 1024
    )

    if gb >= 10:
        return f"{gb:.0f} GB"

    return (
        f"{gb:.1f} GB"
    )


def estimate_format_size(
    item: dict,
    duration: (
        int
        | float
        | None
    ),
) -> int | None:

    # --------------------------------------------------------
    # Exact filesize
    # --------------------------------------------------------

    filesize = (
        item.get(
            "filesize"
        )
    )

    if isinstance(
        filesize,
        (
            int,
            float,
        ),
    ):

        if (
            filesize > 0
        ):

            return int(
                filesize
            )

    # --------------------------------------------------------
    # yt-dlp approximate filesize
    # --------------------------------------------------------

    filesize_approx = (
        item.get(
            "filesize_approx"
        )
    )

    if isinstance(
        filesize_approx,
        (
            int,
            float,
        ),
    ):

        if (
            filesize_approx > 0
        ):

            return int(
                filesize_approx
            )

    # --------------------------------------------------------
    # Calculate from bitrate
    #
    # tbr = total bitrate in Kbit/s
    #
    # bytes =
    # bitrate * 1000 / 8 * duration
    # --------------------------------------------------------

    tbr = (
        item.get(
            "tbr"
        )
    )

    if (
        tbr is None
    ):

        vbr = (
            item.get(
                "vbr"
            )
        )

        abr = (
            item.get(
                "abr"
            )
        )

        try:

            vbr_value = float(
                vbr
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            vbr_value = 0.0

        try:

            abr_value = float(
                abr
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            abr_value = 0.0

        combined = (
            vbr_value
            + abr_value
        )

        if combined > 0:

            tbr = (
                combined
            )

    if (
        tbr is None
        or duration is None
    ):

        return None

    try:

        tbr = float(
            tbr
        )

        duration = float(
            duration
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if (
        tbr <= 0
        or duration <= 0
    ):

        return None

    estimated_bytes = int(
        (
            tbr
            * 1000
            / 8
        )
        * duration
    )

    if (
        estimated_bytes <= 0
    ):

        return None

    return (
        estimated_bytes
    )


# ============================================================
# Speed
# ============================================================

def format_speed(
    speed: (
        int
        | float
        | None
    ),
) -> str | None:

    if (
        speed is None
        or speed <= 0
    ):

        return None

    speed = float(
        speed
    )

    mbps = (
        speed
        / 1024
        / 1024
    )

    if (
        mbps >= 1
    ):

        return (
            f"{mbps:.1f} MB/s"
        )

    kbps = (
        speed
        / 1024
    )

    if (
        kbps >= 1
    ):

        return (
            f"{kbps:.0f} KB/s"
        )

    return (
        f"{speed:.0f} B/s"
    )


# ============================================================
# ETA
# ============================================================

def format_eta(
    seconds: (
        int
        | float
        | None
    ),
) -> str | None:

    if (
        seconds is None
    ):

        return None

    try:

        seconds = max(
            0,
            int(
                seconds
            ),
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if (
        seconds < 60
    ):

        return (
            f"{seconds} ثانیه"
        )

    hours, remainder = divmod(
        seconds,
        3600,
    )

    minutes, secs = divmod(
        remainder,
        60,
    )

    parts: list[
        str
    ] = []

    if hours:

        parts.append(
            f"{hours} ساعت"
        )

    if minutes:

        parts.append(
            f"{minutes} دقیقه"
        )

    if (
        secs
        and not hours
    ):

        parts.append(
            f"{secs} ثانیه"
        )

    if not parts:

        return (
            "کمتر از یک دقیقه"
        )

    return (
        " و ".join(
            parts
        )
    )


# ============================================================
# Progress text
# ============================================================

def build_progress_text(
    job_id: int,
    quality: str,
    job: dict,
    paused: bool = False,
) -> str:

    progress = int(
        job.get(
            "progress",
            0,
        )
        or 0
    )

    downloaded_bytes = (
        job.get(
            "downloaded_bytes"
        )
    )

    total_bytes = (
        job.get(
            "total_bytes"
        )
    )

    speed = (
        job.get(
            "speed"
        )
    )

    eta = (
        job.get(
            "eta"
        )
    )

    downloaded_label = (
        format_file_size(
            downloaded_bytes
        )
    )

    total_label = (
        format_file_size(
            total_bytes
        )
    )

    speed_label = (
        format_speed(
            speed
        )
    )

    eta_label = (
        format_eta(
            eta
        )
    )

    if paused:

        lines = [
            "⏸ <b>دانلود متوقف شده است</b>",
            "",
            (
                f"🆔 Job ID: "
                f"<code>{job_id}</code>"
            ),
            (
                f"🎬 کیفیت: "
                f"<code>{quality}</code>"
            ),
            (
                f"📊 پیشرفت: "
                f"<code>{progress}%</code>"
            ),
        ]

    else:

        lines = [
            "⬇️ <b>در حال دانلود...</b>",
            "",
            (
                f"🆔 Job ID: "
                f"<code>{job_id}</code>"
            ),
            (
                f"🎬 کیفیت: "
                f"<code>{quality}</code>"
            ),
            (
                f"📊 پیشرفت: "
                f"<code>{progress}%</code>"
            ),
        ]

    if (
        downloaded_label
        and total_label
    ):

        lines.append(
            (
                "📦 دانلود شده: "
                f"<code>"
                f"{downloaded_label} / "
                f"{total_label}"
                f"</code>"
            )
        )

    elif downloaded_label:

        lines.append(
            (
                "📦 دانلود شده: "
                f"<code>"
                f"{downloaded_label}"
                f"</code>"
            )
        )

    elif total_label:

        lines.append(
            (
                "📦 حجم کل: "
                f"<code>"
                f"{total_label}"
                f"</code>"
            )
        )

    if not paused:

        if speed_label:

            lines.append(
                (
                    "🚀 سرعت: "
                    f"<code>"
                    f"{speed_label}"
                    f"</code>"
                )
            )

        if eta_label:

            lines.append(
                (
                    "⏳ زمان باقی‌مانده: "
                    f"<code>"
                    f"{eta_label}"
                    f"</code>"
                )
            )

    else:

        lines.extend(
            [
                "",
                (
                    "🕕 فایل نیمه‌کاره تا "
                    "<b>۶ ساعت</b> "
                    "نگهداری می‌شود."
                ),
                (
                    "پس از آن برای آزادسازی "
                    "فضای سرور حذف خواهد شد."
                ),
            ]
        )

    return (
        "\n".join(
            lines
        )
    )


# ============================================================
# Pending selection
# ============================================================

def add_pending_selection(
    source_url: str,
    quality_options: list[
        tuple[
            int,
            int | None,
        ]
    ]
    | None = None,
    playlist_index: int | None = None,
) -> str:

    token = (
        uuid.uuid4()
        .hex[:10]
    )

    sizes: dict[
        int,
        int | None,
    ] = {}

    if quality_options:

        for (
            height,
            file_size,
        ) in quality_options:

            sizes[
                height
            ] = (
                file_size
            )

    PENDING_SELECTIONS[
        token
    ] = {
        "source_url":
            source_url,

        "sizes":
            sizes,

        "playlist_index":
            playlist_index,
    }

    if (
        len(
            PENDING_SELECTIONS
        )
        > 1000
    ):

        oldest_token = next(
            iter(
                PENDING_SELECTIONS
            )
        )

        PENDING_SELECTIONS.pop(
            oldest_token,
            None,
        )

    return token


# ============================================================
# Pending media entries
# ============================================================

def add_pending_media_entries(
    source_url: str,
    entries: list[dict],
) -> str:

    token = (
        uuid.uuid4()
        .hex[:10]
    )

    PENDING_MEDIA_ENTRIES[
        token
    ] = {
        "source_url":
            source_url,

        "entries":
            entries,
    }

    if (
        len(
            PENDING_MEDIA_ENTRIES
        )
        > 1000
    ):

        oldest_token = next(
            iter(
                PENDING_MEDIA_ENTRIES
            )
        )

        PENDING_MEDIA_ENTRIES.pop(
            oldest_token,
            None,
        )

    return token


# ============================================================
# Backend - user
# ============================================================

async def register_telegram_user(
    message: Message,
) -> dict:

    if not message.from_user:

        raise RuntimeError(
            "Telegram user information is missing."
        )

    user = (
        message.from_user
    )

    timeout = (
        aiohttp.ClientTimeout(
            total=15
        )
    )

    params = {
        "telegram_id":
            user.id,

        "username":
            user.username
            or "",

        "first_name":
            user.first_name
            or "",

        "last_name":
            user.last_name
            or "",

        "language_code":
            user.language_code
            or "",
    }

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            f"{BACKEND_URL}/users/telegram",
            params=params,
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )


# ============================================================
# Backend - media info
# ============================================================

async def get_media_info(
    source_url: str,
    playlist_index: int | None = None,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=90
        )
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            f"{BACKEND_URL}/downloads/info",
            params={
                "url":
                    source_url,

                **(
                    {
                        "playlist_index":
                            playlist_index,
                    }
                    if (
                        playlist_index
                        is not None
                    )
                    else {}
                ),
            },
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )


# ============================================================
# Backend - create
# ============================================================

async def create_download_job(
    source_url: str,
    quality: str | None = None,
    media_type: str = "video",
    playlist_index: int | None = None,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=30
        )
    )

    payload = {
        "source_url":
            source_url,

        "quality":
            quality,

        "media_type":
            media_type,

        "playlist_index":
            playlist_index,
    }

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            f"{BACKEND_URL}/downloads",
            json=payload,
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )

# ============================================================
# Backend - get
# ============================================================

async def get_download_job(
    job_id: int,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=15
        )
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            (
                f"{BACKEND_URL}"
                f"/downloads/{job_id}"
            ),
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )


# ============================================================
# Backend actions
# ============================================================

async def _post_job_action(
    job_id: int,
    action: str,
    fallback_error: str,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=15
        )
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            (
                f"{BACKEND_URL}"
                f"/downloads/"
                f"{job_id}/"
                f"{action}"
            ),
        ) as response:

            if (
                response.status
                >= 400
            ):

                try:

                    data = (
                        await response.json()
                    )

                    detail = (
                        data.get(
                            "detail",
                            fallback_error,
                        )
                    )

                except Exception:

                    detail = (
                        await response.text()
                        or fallback_error
                    )

                raise RuntimeError(
                    str(
                        detail
                    )
                )

            return (
                await response.json()
            )


async def pause_download_job(
    job_id: int,
) -> dict:

    return await _post_job_action(
        job_id,
        "pause",
        "Pause failed",
    )


async def resume_download_job(
    job_id: int,
) -> dict:

    return await _post_job_action(
        job_id,
        "resume",
        "Resume failed",
    )


async def cancel_download_job(
    job_id: int,
) -> dict:

    return await _post_job_action(
        job_id,
        "cancel",
        "Cancel failed",
    )


# ============================================================
# Safe message edit
# ============================================================

async def safe_edit_message(
    message: Message,
    text: str,
    reply_markup: (
        InlineKeyboardMarkup
        | None
    ) = None,
) -> bool:

    try:

        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

        return True

    except Exception as exc:

        error_text = (
            str(
                exc
            )
            .lower()
        )

        if (
            "message is not modified"
            not in error_text
        ):

            print(
                "Message edit failed: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        return False


# ============================================================
# Download keyboards
# ============================================================

def build_active_download_keyboard(
    job_id: int,
) -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "⏸ توقف دانلود"
                    ),
                    callback_data=(
                        f"download_pause:"
                        f"{job_id}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "❌ انصراف از دانلود"
                    ),
                    callback_data=(
                        f"download_cancel:"
                        f"{job_id}"
                    ),
                ),
            ],
        ]
    )


def build_paused_download_keyboard(
    job_id: int,
) -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "▶️ ادامه دانلود"
                    ),
                    callback_data=(
                        f"download_resume:"
                        f"{job_id}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "❌ انصراف از دانلود"
                    ),
                    callback_data=(
                        f"download_cancel:"
                        f"{job_id}"
                    ),
                ),
            ],
        ]
    )


# ============================================================
# Resolution helpers
# ============================================================

def _extract_resolution(
    resolution: str | None,
) -> tuple[
    int,
    int,
] | None:

    if not resolution:

        return None

    match = re.fullmatch(
        r"(\d+)x(\d+)",
        str(
            resolution
        ).strip(),
    )

    if not match:

        return None

    try:

        width = int(
            match.group(
                1
            )
        )

        height = int(
            match.group(
                2
            )
        )

    except ValueError:

        return None

    if (
        width <= 0
        or height <= 0
    ):

        return None

    return (
        width,
        height,
    )


def _get_format_quality(
    item: dict,
) -> int | None:

    resolution = (
        _extract_resolution(
            item.get(
                "resolution"
            )
        )
    )

    if (
        resolution
        is None
    ):

        return None

    width, height = (
        resolution
    )

    return min(
        width,
        height,
    )


# ============================================================
# Audio size
# ============================================================

def _estimate_best_audio_size(
    formats: list[dict],
    duration: int | float | None,
) -> int | None:
    """
    Estimate the audio stream size that will be combined with
    a video-only format.

    Normal sites:
        Prefer real audio filesize metadata.

    X / Twitter:
        HLS audio entries often expose only the tiny .m3u8
        manifest size. Their format_id contains the bitrate:

            hls-audio-64000-Audio

        In that case estimate:
            bitrate * duration / 8

        plus a small container/network overhead.
    """

    real_audio_sizes: list[
        int
    ] = []

    preferred_audio_sizes: list[
        int
    ] = []

    x_hls_bitrates: list[
        int
    ] = []

    for item in formats:

        if item.get(
            "has_video"
        ):

            continue

        if not item.get(
            "has_audio"
        ):

            continue

        format_id = (
            str(
                item.get(
                    "format_id"
                )
                or ""
            )
            .strip()
            .lower()
        )

        extension = (
            str(
                item.get(
                    "extension"
                )
                or ""
            )
            .strip()
            .lower()
        )

        audio_codec = (
            str(
                item.get(
                    "audio_codec"
                )
                or ""
            )
            .strip()
            .lower()
        )

        # ----------------------------------------------------
        # X HLS audio
        #
        # filesize for these entries is normally the manifest
        # itself (hundreds of bytes), not the media payload.
        # ----------------------------------------------------

        match = re.search(
            r"hls-audio-(\d+)-audio",
            format_id,
        )

        if match:

            try:

                bitrate = int(
                    match.group(
                        1
                    )
                )

            except ValueError:

                bitrate = 0

            if bitrate > 0:

                x_hls_bitrates.append(
                    bitrate
                )

            # Never use the tiny HLS manifest filesize.
            continue

        # ----------------------------------------------------
        # Normal audio filesize
        # ----------------------------------------------------

        file_size = (
            item.get(
                "filesize"
            )
        )

        if not isinstance(
            file_size,
            int,
        ):

            continue

        if (
            file_size
            <= 4096
        ):

            # Protect against other manifest-like responses.
            continue

        real_audio_sizes.append(
            file_size
        )

        if (
            extension
            in {
                "m4a",
                "mp4",
            }
            and (
                audio_codec.startswith(
                    "mp4a"
                )
                or not audio_codec
            )
        ):

            preferred_audio_sizes.append(
                file_size
            )

    if preferred_audio_sizes:

        return max(
            preferred_audio_sizes
        )

    if real_audio_sizes:

        return max(
            real_audio_sizes
        )

    # --------------------------------------------------------
    # Estimate X audio from bitrate + duration
    # --------------------------------------------------------

    if x_hls_bitrates:

        try:

            duration_value = float(
                duration
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            duration_value = 0

        if duration_value > 0:

            bitrate = max(
                x_hls_bitrates
            )

            estimated = (
                bitrate
                * duration_value
                / 8
            )

            # Small overhead for container/segments.
            estimated *= 1.03

            return int(
                estimated
            )

    return None


# ============================================================
# Worker video format simulation
# ============================================================

def _select_worker_video_format(
    formats: list[dict],
    quality: int,
) -> dict | None:

    candidates: list[
        dict
    ] = []

    for item in formats:

        if not item.get(
            "has_video"
        ):

            continue

        item_quality = (
            _get_format_quality(
                item
            )
        )

        if (
            item_quality
            != quality
        ):

            continue

        candidates.append(
            item
        )

    if not candidates:

        return None

    # ========================================================
    # 1. X/Twitter direct HTTP media
    #
    # Example:
    #   http-950 -> 401772 bytes
    #
    # Prefer these over:
    #   hls-316 -> ~498 byte manifest
    # ========================================================

    http_candidates = [
        item
        for item
        in candidates
        if (
            str(
                item.get(
                    "format_id"
                )
                or ""
            )
            .lower()
            .startswith(
                "http-"
            )
            and isinstance(
                item.get(
                    "filesize"
                ),
                int,
            )
            and item.get(
                "filesize"
            )
            > 4096
        )
    ]

    if http_candidates:

        return max(
            http_candidates,
            key=lambda item: (
                item.get(
                    "filesize"
                )
                or 0
            ),
        )

    # ========================================================
    # Remove HLS manifest entries when a normal alternative
    # exists.
    # ========================================================

    non_hls_candidates = [
        item
        for item
        in candidates
        if not (
            str(
                item.get(
                    "format_id"
                )
                or ""
            )
            .lower()
            .startswith(
                "hls-"
            )
        )
    ]

    usable_candidates = (
        non_hls_candidates
        or candidates
    )

    # ========================================================
    # 2. Progressive video+audio
    # ========================================================

    progressive = [
        item
        for item
        in usable_candidates
        if (
            item.get(
                "has_video"
            )
            and item.get(
                "has_audio"
            )
        )
    ]

    if progressive:

        return max(
            progressive,
            key=lambda item: (
                item.get(
                    "filesize"
                )
                or 0
            ),
        )

    # ========================================================
    # 3. MP4 video-only
    # ========================================================

    mp4_video = [
        item
        for item
        in usable_candidates
        if (
            str(
                item.get(
                    "extension"
                )
                or ""
            )
            .lower()
            == "mp4"
            and not item.get(
                "has_audio"
            )
        )
    ]

    if mp4_video:

        return max(
            mp4_video,
            key=lambda item: (
                item.get(
                    "filesize"
                )
                or 0
            ),
        )

    # ========================================================
    # 4. Generic fallback
    # ========================================================

    return max(
        usable_candidates,
        key=lambda item: (
            item.get(
                "filesize"
            )
            or 0
        ),
    )


# ============================================================
# Extract qualities + estimated size
# ============================================================

def extract_available_quality_options(
    media_info: dict,
) -> list[
    tuple[
        int,
        int | None,
    ]
]:

    formats = (
        media_info.get(
            "formats",
            [],
        )
        or []
    )

    duration = (
        media_info.get(
            "duration"
        )
    )

    available_qualities: set[
        int
    ] = set()

    for item in formats:

        if not item.get(
            "has_video"
        ):

            continue

        quality = (
            _get_format_quality(
                item
            )
        )

        if (
            quality is None
            or quality <= 0
        ):

            continue

        available_qualities.add(
            quality
        )

    audio_size = (
        _estimate_best_audio_size(
            formats,
            duration,
        )
    )

    result: list[
        tuple[
            int,
            int | None,
        ]
    ] = []

    for quality in sorted(
        available_qualities
    ):

        selected_format = (
            _select_worker_video_format(
                formats,
                quality,
            )
        )

        approximate_size: (
            int
            | None
        ) = None

        if selected_format:

            video_size = (
                selected_format.get(
                    "filesize"
                )
            )

            has_audio = bool(
                selected_format.get(
                    "has_audio"
                )
            )

            if (
                isinstance(
                    video_size,
                    int,
                )
                and video_size > 0
            ):

                # HLS manifest sizes are not media sizes.
                format_id = (
                    str(
                        selected_format.get(
                            "format_id"
                        )
                        or ""
                    )
                    .lower()
                )

                if (
                    format_id.startswith(
                        "hls-"
                    )
                    and video_size
                    <= 4096
                ):

                    video_size = None

            if (
                isinstance(
                    video_size,
                    int,
                )
                and video_size > 0
            ):

                approximate_size = (
                    video_size
                )

                format_id = (
                    str(
                        selected_format.get(
                            "format_id"
                        )
                        or ""
                    )
                    .strip()
                    .lower()
                )

                # X/Twitter direct HTTP MP4 sizes are already
                # very close to the final downloaded media.
                # Do not double-count a separate HLS audio
                # estimate for these formats.
                is_x_http_format = (
                    format_id.startswith(
                        "http-"
                    )
                )

                if (
                    not has_audio
                    and audio_size
                    and not is_x_http_format
                ):

                    approximate_size += (
                        audio_size
                    )

        result.append(
            (
                quality,
                approximate_size,
            )
        )

    return result


# ============================================================
# Multi-video keyboard
# ============================================================

MEDIA_ENTRY_PAGE_SIZE = 10


def build_media_entry_keyboard(
    entries: list[dict],
    token: str,
    page: int = 0,
) -> InlineKeyboardMarkup:

    rows: list[
        list[
            InlineKeyboardButton
        ]
    ] = []

    total_entries = (
        len(
            entries
        )
    )

    total_pages = max(
        1,
        (
            total_entries
            + MEDIA_ENTRY_PAGE_SIZE
            - 1
        )
        // MEDIA_ENTRY_PAGE_SIZE,
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    start_index = (
        page
        * MEDIA_ENTRY_PAGE_SIZE
    )

    end_index = (
        start_index
        + MEDIA_ENTRY_PAGE_SIZE
    )

    page_entries = (
        entries[
            start_index:
            end_index
        ]
    )

    for entry in page_entries:

        if not isinstance(
            entry,
            dict,
        ):

            continue

        try:

            index = int(
                entry.get(
                    "index"
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        media_type = (
            str(
                entry.get(
                    "media_type"
                )
                or "video"
            )
            .strip()
            .lower()
        )

        if media_type == "image":

            button_text = (
                f"📷 عکس {index}"
            )

        else:

            # Backward compatible:
            # entries without media_type
            # are still treated as video.
            button_text = (
                f"🎬 ویدئو {index}"
            )

        rows.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"media_entry:"
                        f"{token}:"
                        f"{index}"
                    ),
                )
            ]
        )

    # ========================================================
    # Pagination navigation
    # ========================================================

    if total_pages > 1:

        navigation_row: list[
            InlineKeyboardButton
        ] = []

        if page > 0:

            navigation_row.append(
                InlineKeyboardButton(
                    text="⬅️ قبلی",
                    callback_data=(
                        f"media_page:"
                        f"{token}:"
                        f"{page - 1}"
                    ),
                )
            )

        navigation_row.append(
            InlineKeyboardButton(
                text=(
                    f"📄 "
                    f"{page + 1}"
                    f"/"
                    f"{total_pages}"
                ),
                callback_data=(
                    "media_page_info"
                ),
            )
        )

        if page < (
            total_pages - 1
        ):

            navigation_row.append(
                InlineKeyboardButton(
                    text="بعدی ➡️",
                    callback_data=(
                        f"media_page:"
                        f"{token}:"
                        f"{page + 1}"
                    ),
                )
            )

        rows.append(
            navigation_row
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# Quality keyboard
# ============================================================

def build_quality_keyboard(
    quality_options: list[
        tuple[
            int,
            int | None,
        ]
    ],
    token: str,
) -> InlineKeyboardMarkup:

    buttons: list[
        list[
            InlineKeyboardButton
        ]
    ] = []

    for index in range(
        0,
        len(
            quality_options
        ),
        2,
    ):

        row: list[
            InlineKeyboardButton
        ] = []

        for (
            height,
            file_size,
        ) in quality_options[
            index:
            index + 2
        ]:

            quality_label = (
                normalize_quality_label(
                    height
                )
            )

            size_label = (
                format_file_size(
                    file_size
                )
            )

            if size_label:

                button_text = (
                    f"🎬 "
                    f"{quality_label}"
                    f" • ~"
                    f"{size_label}"
                )

            else:

                button_text = (
                    f"🎬 "
                    f"{quality_label}"
                )

            row.append(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"quality:"
                        f"{token}:"
                        f"{height}"
                    ),
                )
            )

        buttons.append(
            row
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# Smaller quality keyboard
# ============================================================

def build_smaller_quality_keyboard(
    source_url: str,
    selected_height: int,
    sizes: dict[
        int,
        int | None,
    ],
    playlist_index: int | None = None,
) -> InlineKeyboardMarkup | None:

    quality_options: list[
        tuple[
            int,
            int | None,
        ]
    ] = []

    for height in sorted(
        sizes.keys()
    ):

        if (
            height
            >= selected_height
        ):

            continue

        file_size = (
            sizes.get(
                height
            )
        )

        if (
            file_size is not None
            and file_size
            > MAX_DOWNLOAD_SIZE_BYTES
        ):

            continue

        quality_options.append(
            (
                height,
                file_size,
            )
        )

    if not quality_options:

        return None

    token = (
        add_pending_selection(
            source_url,
            quality_options,
            playlist_index=(
                playlist_index
            ),
        )
    )

    return (
        build_quality_keyboard(
            quality_options=(
                quality_options
            ),
            token=token,
        )
    )

# ============================================================
# Wait for download
# ============================================================

async def wait_for_download(
    job_id: int,
    message: Message,
    quality: str,
) -> dict:

    elapsed = 0

    last_render_key: (
        tuple
        | None
    ) = None

    while (
        elapsed
        < MAX_WAIT_TIME
    ):

        try:

            job = (
                await get_download_job(
                    job_id
                )
            )

        except Exception as exc:

            print(
                "Failed to get job "
                f"{job_id}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            await asyncio.sleep(
                POLL_INTERVAL
            )

            elapsed += (
                POLL_INTERVAL
            )

            continue

        status = (
            job.get(
                "status"
            )
        )

        progress = int(
            job.get(
                "progress",
                0,
            )
            or 0
        )

        speed_bucket = int(
            float(
                job.get(
                    "speed"
                )
                or 0
            )
            / (
                128
                * 1024
            )
        )

        render_key = (
            status,
            progress,
            job.get(
                "downloaded_bytes"
            ),
            job.get(
                "total_bytes"
            ),
            speed_bucket,
            job.get(
                "eta"
            ),
        )

        if (
            status
            == "pending"
        ):

            if (
                render_key
                != last_render_key
            ):

                await safe_edit_message(
                    message,
                    (
                        "⏳ <b>درخواست در صف دانلود است</b>\n\n"

                        f"🆔 Job ID: "
                        f"<code>{job_id}</code>\n"

                        f"🎬 کیفیت: "
                        f"<code>{quality}</code>\n"

                        "📊 وضعیت: "
                        "<code>pending</code>"
                    ),
                    reply_markup=(
                        build_active_download_keyboard(
                            job_id
                        )
                    ),
                )

                last_render_key = (
                    render_key
                )

        elif (
            status
            == "processing"
        ):

            if (
                render_key
                != last_render_key
            ):

                await safe_edit_message(
                    message,
                    build_progress_text(
                        job_id=job_id,
                        quality=quality,
                        job=job,
                        paused=False,
                    ),
                    reply_markup=(
                        build_active_download_keyboard(
                            job_id
                        )
                    ),
                )

                last_render_key = (
                    render_key
                )

        elif (
            status
            == "paused"
        ):

            if (
                render_key
                != last_render_key
            ):

                await safe_edit_message(
                    message,
                    build_progress_text(
                        job_id=job_id,
                        quality=quality,
                        job=job,
                        paused=True,
                    ),
                    reply_markup=(
                        build_paused_download_keyboard(
                            job_id
                        )
                    ),
                )

                last_render_key = (
                    render_key
                )

        elif (
            status
            == "completed"
        ):

            return job

        elif (
            status
            == "cancelled"
        ):

            downloaded_label = (
                format_file_size(
                    job.get(
                        "downloaded_bytes"
                    )
                )
            )

            extra = ""

            if downloaded_label:

                extra = (
                    "\n📦 دانلود شده تا زمان لغو: "
                    f"<code>"
                    f"{downloaded_label}"
                    f"</code>"
                )

            await safe_edit_message(
                message,
                (
                    "❌ <b>دانلود لغو شد</b>\n\n"

                    f"🆔 Job ID: "
                    f"<code>{job_id}</code>\n"

                    f"📊 پیشرفت هنگام لغو: "
                    f"<code>{progress}%</code>"
                    f"{extra}\n\n"

                    "🗑 فایل‌های موقت از سرور حذف شدند."
                ),
                reply_markup=None,
            )

            return job

        elif (
            status
            == "expired"
        ):

            await safe_edit_message(
                message,
                (
                    "⌛ <b>دانلود منقضی شد</b>\n\n"

                    f"🆔 Job ID: "
                    f"<code>{job_id}</code>\n\n"

                    "بیش از ۶ ساعت از توقف دانلود گذشته بود.\n"

                    "🗑 فایل موقت برای آزادسازی "
                    "فضای سرور حذف شد."
                ),
                reply_markup=None,
            )

            return job

        elif (
            status
            == "failed"
        ):

            error = (
                job.get(
                    "error_message"
                )
                or
                "خطای نامشخص"
            )

            raise RuntimeError(
                error
            )

        await asyncio.sleep(
            POLL_INTERVAL
        )

        elapsed += (
            POLL_INTERVAL
        )

    raise asyncio.TimeoutError(
        "Download timed out"
    )


# ============================================================
# Send downloaded file
# ============================================================

async def send_downloaded_file(
    message: Message,
    status_message: Message,
    job: dict,
) -> None:

    job_id = (
        job[
            "id"
        ]
    )

    file_path = (
        job.get(
            "file_path"
        )
    )

    if not file_path:

        raise RuntimeError(
            "Downloaded file path is missing"
        )

    path = Path(
        file_path
    )

    if not path.exists():

        raise FileNotFoundError(
            f"File not found: "
            f"{path}"
        )

    file_size = (
        path.stat()
        .st_size
    )

    if (
        file_size <= 0
    ):

        raise RuntimeError(
            "Downloaded file is empty"
        )

    if (
        file_size
        > MAX_DOWNLOAD_SIZE_BYTES
    ):

        try:

            path.unlink()

        except Exception:

            pass

        raise RuntimeError(
            "Downloaded file exceeds "
            f"{MAX_DOWNLOAD_SIZE_MB} MB limit"
        )

    size_label = (
        format_file_size(
            file_size
        )
        or
        f"{file_size} bytes"
    )

    await safe_edit_message(
        status_message,
        (
            "✅ <b>دانلود کامل شد</b>\n\n"

            f"🆔 Job ID: "
            f"<code>{job_id}</code>\n"

            f"📦 حجم فایل: "
            f"<code>{size_label}</code>\n\n"

            "📤 <b>در حال ارسال فایل به تلگرام...</b>"
        ),
        reply_markup=None,
    )

    suffix = (
        path.suffix
        or ".mp4"
    )

    document = (
        FSInputFile(
            path=str(
                path
            ),
            filename=(
                f"MediaHub-"
                f"{job_id}"
                f"{suffix}"
            ),
        )
    )

    try:

        await message.answer_document(
            document=document,
            caption=(
                "✅ <b>دانلود با موفقیت انجام شد</b>\n\n"

                f"🆔 Job ID: "
                f"<code>{job_id}</code>\n"

                f"📦 حجم فایل: "
                f"<code>{size_label}</code>"
            ),
            parse_mode="HTML",
        )

        print(
            "Successfully sent file: "
            f"{path}"
        )

    finally:

        try:

            path.unlink()

            print(
                "Deleted downloaded file: "
                f"{path}"
            )

        except FileNotFoundError:

            pass

        except Exception as exc:

            print(
                "Failed to delete file "
                f"{path}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


# ============================================================
# Start
# ============================================================

@dp.message(
    CommandStart()
)
async def start_handler(
    message: Message,
):

    try:

        user = (
            await register_telegram_user(
                message
            )
        )

        user_id = (
            user[
                "id"
            ]
        )

        await message.answer(
            (
                "👋 <b>به MediaHub AI خوش آمدید!</b>\n\n"

                "🎬 لینک ویدئو را ارسال کنید "
                "تا دانلود شود.\n\n"

                f"🆔 User ID: "
                f"<code>{user_id}</code>"
            ),
            parse_mode="HTML",
        )

    except Exception as exc:

        print(
            "User registration failed: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        await message.answer(
            (
                "❌ <b>خطا در ثبت اطلاعات کاربر</b>\n\n"

                "لطفاً چند لحظه بعد دوباره تلاش کنید."
            ),
            parse_mode="HTML",
        )


# ============================================================
# Pause
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "download_pause:"
    )
)
async def download_pause_callback(
    callback: CallbackQuery,
):

    if not callback.data:

        return

    try:

        job_id = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )

    except (
        ValueError,
        IndexError,
    ):

        await callback.answer(
            "❌ Job نامعتبر است.",
            show_alert=True,
        )

        return

    try:

        job = (
            await pause_download_job(
                job_id
            )
        )

        await callback.answer(
            "⏸ دانلود متوقف شد."
        )

        if isinstance(
            callback.message,
            Message,
        ):

            quality = str(
                job.get(
                    "quality"
                )
                or
                "نامشخص"
            )

            await safe_edit_message(
                callback.message,
                build_progress_text(
                    job_id=job_id,
                    quality=quality,
                    job=job,
                    paused=True,
                ),
                reply_markup=(
                    build_paused_download_keyboard(
                        job_id
                    )
                ),
            )

    except Exception as exc:

        await callback.answer(
            f"❌ {str(exc)[:150]}",
            show_alert=True,
        )


# ============================================================
# Resume
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "download_resume:"
    )
)
async def download_resume_callback(
    callback: CallbackQuery,
):

    if not callback.data:

        return

    try:

        job_id = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )

    except (
        ValueError,
        IndexError,
    ):

        await callback.answer(
            "❌ Job نامعتبر است.",
            show_alert=True,
        )

        return

    try:

        job = (
            await resume_download_job(
                job_id
            )
        )

        progress = int(
            job.get(
                "progress",
                0,
            )
            or 0
        )

        quality = str(
            job.get(
                "quality"
            )
            or
            "نامشخص"
        )

        downloaded_label = (
            format_file_size(
                job.get(
                    "downloaded_bytes"
                )
            )
        )

        extra = ""

        if downloaded_label:

            extra = (
                "\n📦 دانلود شده: "
                f"<code>"
                f"{downloaded_label}"
                f"</code>"
            )

        await callback.answer(
            "▶️ دانلود ادامه پیدا کرد."
        )

        if isinstance(
            callback.message,
            Message,
        ):

            await safe_edit_message(
                callback.message,
                (
                    "▶️ <b>دانلود ادامه پیدا کرد</b>\n\n"

                    f"🆔 Job ID: "
                    f"<code>{job_id}</code>\n"

                    f"🎬 کیفیت: "
                    f"<code>{quality}</code>\n"

                    f"📊 ادامه از حدود: "
                    f"<code>{progress}%</code>"
                    f"{extra}"
                ),
                reply_markup=(
                    build_active_download_keyboard(
                        job_id
                    )
                ),
            )

    except Exception as exc:

        await callback.answer(
            f"❌ {str(exc)[:150]}",
            show_alert=True,
        )


# ============================================================
# Cancel
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "download_cancel:"
    )
)
async def download_cancel_callback(
    callback: CallbackQuery,
):

    if not callback.data:

        return

    try:

        job_id = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )

    except (
        ValueError,
        IndexError,
    ):

        await callback.answer(
            "❌ Job نامعتبر است.",
            show_alert=True,
        )

        return

    try:

        job = (
            await cancel_download_job(
                job_id
            )
        )

        progress = int(
            job.get(
                "progress",
                0,
            )
            or 0
        )

        downloaded_label = (
            format_file_size(
                job.get(
                    "downloaded_bytes"
                )
            )
        )

        extra = ""

        if downloaded_label:

            extra = (
                "\n📦 دانلود شده تا زمان لغو: "
                f"<code>"
                f"{downloaded_label}"
                f"</code>"
            )

        await callback.answer(
            "❌ دانلود لغو شد."
        )

        if isinstance(
            callback.message,
            Message,
        ):

            await safe_edit_message(
                callback.message,
                (
                    "❌ <b>دانلود لغو شد</b>\n\n"

                    f"🆔 Job ID: "
                    f"<code>{job_id}</code>\n"

                    f"📊 پیشرفت هنگام لغو: "
                    f"<code>{progress}%</code>"
                    f"{extra}\n\n"

                    "🗑 فایل موقت از سرور حذف می‌شود."
                ),
                reply_markup=None,
            )

    except Exception as exc:

        await callback.answer(
            f"❌ {str(exc)[:150]}",
            show_alert=True,
        )


# ============================================================
# Media pagination
# ============================================================

@dp.callback_query(
    F.data == "media_page_info"
)
async def media_page_info_callback(
    callback: CallbackQuery,
):

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "media_page:"
    )
)
async def media_page_callback(
    callback: CallbackQuery,
):

    if not callback.data:

        return

    parts = (
        callback.data.split(
            ":",
            2,
        )
    )

    if (
        len(
            parts
        )
        != 3
    ):

        await callback.answer(
            "❌ درخواست نامعتبر است.",
            show_alert=True,
        )

        return

    (
        _,
        media_token,
        page_text,
    ) = parts

    selection = (
        PENDING_MEDIA_ENTRIES.get(
            media_token
        )
    )

    if not selection:

        await callback.answer(
            (
                "⌛ این انتخاب منقضی شده است. "
                "لینک را دوباره ارسال کنید."
            ),
            show_alert=True,
        )

        return

    try:

        page = int(
            page_text
        )

    except (
        TypeError,
        ValueError,
    ):

        await callback.answer(
            "❌ شماره صفحه نامعتبر است.",
            show_alert=True,
        )

        return

    entries = (
        selection.get(
            "entries"
        )
        or []
    )

    total_pages = max(
        1,
        (
            len(
                entries
            )
            + MEDIA_ENTRY_PAGE_SIZE
            - 1
        )
        // MEDIA_ENTRY_PAGE_SIZE,
    )

    if (
        page < 0
        or page >= total_pages
    ):

        await callback.answer(
            "❌ این صفحه وجود ندارد.",
            show_alert=True,
        )

        return

    message = (
        callback.message
    )

    if not isinstance(
        message,
        Message,
    ):

        return

    keyboard = (
        build_media_entry_keyboard(
            entries=entries,
            token=media_token,
            page=page,
        )
    )

    await callback.answer(
        (
            f"صفحه "
            f"{page + 1}"
            f" از "
            f"{total_pages}"
        )
    )

    try:

        await message.edit_reply_markup(
            reply_markup=keyboard
        )

    except Exception as exc:

        error_text = (
            str(
                exc
            )
            .strip()
            .lower()
        )

        if (
            "message is not modified"
            not in error_text
        ):

            print(
                "Media pagination edit failed: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


# ============================================================
# Multi-video entry callback
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "media_entry:"
    )
)
async def media_entry_callback(
    callback: CallbackQuery,
):

    if not callback.data:

        return

    parts = (
        callback.data.split(
            ":",
            2,
        )
    )

    if (
        len(
            parts
        )
        != 3
    ):

        await callback.answer(
            "❌ درخواست نامعتبر است.",
            show_alert=True,
        )

        return

    (
        _,
        media_token,
        index_text,
    ) = parts

    selection = (
        PENDING_MEDIA_ENTRIES.get(
            media_token
        )
    )

    if not selection:

        await callback.answer(
            (
                "⌛ این انتخاب منقضی شده است. "
                "لینک را دوباره ارسال کنید."
            ),
            show_alert=True,
        )

        return

    try:

        index = int(
            index_text
        )

    except (
        TypeError,
        ValueError,
    ):

        await callback.answer(
            "❌ شماره رسانه نامعتبر است.",
            show_alert=True,
        )

        return

    source_url = (
        selection.get(
            "source_url"
        )
    )

    entries = (
        selection.get(
            "entries"
        )
        or []
    )

    if not source_url:

        await callback.answer(
            "❌ لینک رسانه پیدا نشد.",
            show_alert=True,
        )

        return

    selected_entry = next(
        (
            entry
            for entry in entries
            if (
                isinstance(
                    entry,
                    dict,
                )
                and str(
                    entry.get(
                        "index"
                    )
                )
                == str(
                    index
                )
            )
        ),
        None,
    )

    if selected_entry is None:

        await callback.answer(
            "❌ رسانه انتخاب‌شده پیدا نشد.",
            show_alert=True,
        )

        return

    media_type = (
        str(
            selected_entry.get(
                "media_type"
            )
            or "video"
        )
        .strip()
        .lower()
    )

    message = (
        callback.message
    )

    if not isinstance(
        message,
        Message,
    ):

        return

    # The selection token is one-time.
    PENDING_MEDIA_ENTRIES.pop(
        media_token,
        None,
    )

    # ========================================================
    # IMAGE
    # ========================================================

    if media_type == "image":

        await callback.answer(
            f"📷 عکس {index} انتخاب شد."
        )

        try:

            await safe_edit_message(
                message,
                (
                    "⏳ <b>در حال ایجاد درخواست دانلود عکس...</b>\n\n"
                    f"📷 شماره عکس: "
                    f"<code>{index}</code>\n"
                    "🖼 کیفیت: "
                    "<code>اصلی</code>"
                ),
            )

            job = (
                await create_download_job(
                    source_url=source_url,
                    quality=None,
                    media_type="image",
                    playlist_index=index,
                )
            )

            job_id = (
                job[
                    "id"
                ]
            )

            await safe_edit_message(
                message,
                (
                    "✅ <b>درخواست دانلود عکس ایجاد شد</b>\n\n"
                    f"🆔 Job ID: "
                    f"<code>{job_id}</code>\n"
                    f"📷 عکس: "
                    f"<code>{index}</code>\n"
                    "🖼 کیفیت: "
                    "<code>اصلی</code>\n"
                    "📊 وضعیت: "
                    "<code>pending</code>"
                ),
                reply_markup=(
                    build_active_download_keyboard(
                        job_id
                    )
                ),
            )

            completed_job = (
                await wait_for_download(
                    job_id,
                    message,
                    "تصویر اصلی",
                )
            )

            final_status = (
                completed_job.get(
                    "status"
                )
            )

            if final_status != "completed":

                return

            await send_downloaded_file(
                message=message,
                status_message=message,
                job=completed_job,
            )

        except Exception as exc:

            print(
                "Image download callback error: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            await safe_edit_message(
                message,
                (
                    "❌ <b>دانلود عکس با خطا مواجه شد</b>\n\n"
                    "⚠️ خطا:\n"
                    f"<code>"
                    f"{html.escape(str(exc)[:1000])}"
                    f"</code>"
                ),
                reply_markup=None,
            )

        return

    # ========================================================
    # VIDEO
    #
    # Existing X / Instagram video flow:
    # retrieve the selected entry's real formats, then show
    # the normal quality keyboard.
    # ========================================================

    await callback.answer(
        f"🎬 ویدئو {index} انتخاب شد."
    )

    try:

        selected_info = (
            await get_media_info(
                source_url=source_url,
                playlist_index=index,
            )
        )

        quality_options = (
            extract_available_quality_options(
                selected_info
            )
        )

        quality_options = (
            normalize_quality_options(
                quality_options
            )
        )

        if not quality_options:

            raise RuntimeError(
                "هیچ کیفیت ویدئویی "
                "قابل دانلودی پیدا نشد."
            )

        token = (
            add_pending_selection(
                source_url,
                quality_options,
                playlist_index=index,
            )
        )

        keyboard = (
            build_quality_keyboard(
                quality_options=(
                    quality_options
                ),
                token=token,
            )
        )

        title = (
            normalize_media_title(
                source_url=source_url,
                title=(
                    selected_info.get(
                        "title"
                    )
                ),
            )
        )

        safe_title = (
            html.escape(
                str(
                    title
                )
            )
        )

        await safe_edit_message(
            message,
            (
                f"🎬 <b>ویدئو {index} آماده دانلود است</b>\n\n"
                f"📌 <b>عنوان:</b> "
                f"{safe_title}\n\n"
                "🎯 <b>کیفیت موردنظر را انتخاب کنید:</b>\n"
                "📦 حجم‌ها تقریبی هستند."
            ),
            reply_markup=keyboard,
        )

    except Exception as exc:

        print(
            "Media entry error: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        await safe_edit_message(
            message,
            (
                "❌ <b>دریافت اطلاعات رسانه ناموفق بود</b>\n\n"
                "⚠️ خطا:\n"
                f"<code>"
                f"{html.escape(str(exc)[:1000])}"
                f"</code>"
            ),
            reply_markup=None,
        )


# ============================================================
# Quality callback
# ============================================================

@dp.callback_query(
    F.data.startswith(
        "quality:"
    )
)
async def quality_callback(
    callback: CallbackQuery,
):

    if not callback.data:

        await callback.answer(
            "❌ درخواست نامعتبر است.",
            show_alert=True,
        )

        return

    parts = (
        callback.data.split(
            ":",
            2,
        )
    )

    if (
        len(
            parts
        )
        != 3
    ):

        await callback.answer(
            "❌ درخواست نامعتبر است.",
            show_alert=True,
        )

        return

    (
        _,
        token,
        height_text,
    ) = parts

    selection = (
        PENDING_SELECTIONS.get(
            token
        )
    )

    if not selection:

        await callback.answer(
            (
                "⏰ این درخواست منقضی شده است. "
                "لطفاً لینک را دوباره ارسال کنید."
            ),
            show_alert=True,
        )

        return

    source_url = (
        selection.get(
            "source_url"
        )
    )

    sizes = (
        selection.get(
            "sizes",
            {},
        )
    )

    playlist_index = (
        selection.get(
            "playlist_index"
        )
    )

    if not source_url:

        await callback.answer(
            "❌ لینک دانلود پیدا نشد.",
            show_alert=True,
        )

        return

    try:

        height = int(
            height_text
        )

    except ValueError:

        await callback.answer(
            "❌ کیفیت نامعتبر است.",
            show_alert=True,
        )

        return

    quality = (
        normalize_quality_label(
            height
        )
    )

    estimated_size = (
        sizes.get(
            height
        )
    )

    if (
        estimated_size is not None
        and estimated_size
        > MAX_DOWNLOAD_SIZE_BYTES
    ):

        size_label = (
            format_file_size(
                estimated_size
            )
            or
            "نامشخص"
        )

        await callback.answer(
            (
                "حجم این کیفیت "
                "بیش از حد مجاز است."
            ),
            show_alert=True,
        )

        message = (
            callback.message
        )

        if not isinstance(
            message,
            Message,
        ):

            return

        smaller_keyboard = (
            build_smaller_quality_keyboard(
                source_url=source_url,
                selected_height=height,
                sizes=sizes,
                playlist_index=(
                    playlist_index
                ),
            )
        )

        text = (
            "⚠️ <b>حجم این کیفیت بیش از حد مجاز است</b>\n\n"

            f"🎬 کیفیت انتخاب‌شده: "
            f"<code>{quality}</code>\n"

            f"📦 حجم تقریبی: "
            f"<code>{size_label}</code>\n\n"
        )

        if smaller_keyboard:

            text += (
                "👇 لطفاً یکی از کیفیت‌های "
                "پایین‌تر را انتخاب کنید."
            )

        else:

            text += (
                "❌ کیفیت پایین‌تری در "
                "محدوده مجاز پیدا نشد."
            )

        await safe_edit_message(
            message,
            text,
            reply_markup=(
                smaller_keyboard
            ),
        )

        PENDING_SELECTIONS.pop(
            token,
            None,
        )

        return

    await callback.answer(
        (
            f"کیفیت "
            f"{quality} "
            "انتخاب شد."
        )
    )

    PENDING_SELECTIONS.pop(
        token,
        None,
    )

    message = (
        callback.message
    )

    if not isinstance(
        message,
        Message,
    ):

        return

    status_message = (
        message
    )

    try:

        estimated_text = ""

        if estimated_size:

            estimated_label = (
                format_file_size(
                    estimated_size
                )
            )

            if estimated_label:

                estimated_text = (
                    "\n📦 حجم تقریبی: "
                    f"<code>"
                    f"{estimated_label}"
                    f"</code>"
                )

        await safe_edit_message(
            status_message,
            (
                "⏳ <b>در حال ایجاد درخواست دانلود...</b>\n\n"

                f"🎬 کیفیت: "
                f"<code>{quality}</code>"
                f"{estimated_text}"
            ),
        )

        job = (
            await create_download_job(
                source_url=source_url,
                quality=quality,
                playlist_index=(
                    playlist_index
                ),
            )
        )

        job_id = (
            job[
                "id"
            ]
        )

        await safe_edit_message(
            status_message,
            (
                "✅ <b>درخواست دانلود ایجاد شد</b>\n\n"

                f"🆔 Job ID: "
                f"<code>{job_id}</code>\n"

                f"🎬 کیفیت: "
                f"<code>{quality}</code>\n"

                "📊 وضعیت: "
                "<code>pending</code>"
            ),
            reply_markup=(
                build_active_download_keyboard(
                    job_id
                )
            ),
        )

        completed_job = (
            await wait_for_download(
                job_id,
                status_message,
                quality,
            )
        )

        final_status = (
            completed_job.get(
                "status"
            )
        )

        if (
            final_status
            != "completed"
        ):

            return

        await send_downloaded_file(
            message,
            status_message,
            completed_job,
        )

        try:

            await (
                status_message.delete()
            )

        except Exception as exc:

            print(
                "Status message delete failed: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    except asyncio.TimeoutError:

        await safe_edit_message(
            status_message,
            (
                "⌛ <b>زمان انتظار ربات به پایان رسید</b>\n\n"

                "وضعیت Job را دوباره بررسی کنید."
            ),
            reply_markup=None,
        )

    except Exception as exc:

        print(
            "Download error: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        error_text = (
            str(
                exc
            )[:1000]
        )

        if (
            is_youtube_url(
                source_url
            )
            and height > 360
            and "403"
            in error_text
        ):

            fallback_options = [
                (
                    360,
                    sizes.get(
                        360
                    ),
                )
            ]

            fallback_token = (
                add_pending_selection(
                    source_url,
                    fallback_options,
                )
            )

            fallback_keyboard = (
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=(
                                    "🎬 دانلود با 360p"
                                ),
                                callback_data=(
                                    f"quality:"
                                    f"{fallback_token}:"
                                    "360"
                                ),
                            )
                        ]
                    ]
                )
            )

            await safe_edit_message(
                status_message,
                (
                    f"❌ <b>کیفیت "
                    f"{quality} "
                    "فعلاً از YouTube "
                    "قابل دریافت نیست</b>\n\n"

                    "✅ کیفیت "
                    "<code>360p</code> "
                    "در دسترس است."
                ),
                reply_markup=(
                    fallback_keyboard
                ),
            )

            return

        safe_error = (
            html.escape(
                error_text
            )
        )

        await safe_edit_message(
            status_message,
            (
                "❌ <b>دانلود انجام نشد</b>\n\n"

                "⚠️ خطا:\n"

                f"<code>"
                f"{safe_error}"
                f"</code>"
            ),
            reply_markup=None,
        )


# ============================================================
# URL handler
# ============================================================

@dp.message(
    F.text
)
async def download_handler(
    message: Message,
):

    source_url = (
        extract_url(
            message.text
            or ""
        )
    )

    if not source_url:

        return

    status_message = (
        await message.answer(
            (
                "🔎 <b>لینک دریافت شد</b>\n"

                "در حال بررسی کیفیت‌های موجود..."
            ),
            parse_mode="HTML",
        )
    )

    try:

        media_info = (
            await get_media_info(
                source_url
            )
        )

        title = (
            normalize_media_title(
                source_url=source_url,
                title=(
                    media_info.get(
                        "title"
                    )
                ),
            )
        )

        duration = (
            media_info.get(
                "duration"
            )
        )

        is_playlist = bool(
            media_info.get(
                "is_playlist"
            )
        )

        entries = (
            media_info.get(
                "entries"
            )
            or []
        )

        if (
            is_playlist
            and len(
                entries
            )
            > 1
        ):

            media_token = (
                add_pending_media_entries(
                    source_url,
                    entries,
                )
            )

            keyboard = (
                build_media_entry_keyboard(
                    entries=entries,
                    token=media_token,
                )
            )

            safe_title = (
                html.escape(
                    str(
                        title
                    )
                )
            )

            await status_message.edit_text(
                (
                    "📚 <b>این پست شامل "
                    f"{len(entries)} رسانه است</b>\n\n"

                    f"📌 <b>عنوان:</b> "
                    f"{safe_title}\n\n"

                    "👇 <b>رسانه موردنظر را انتخاب کنید:</b>"
                ),
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            return

        # Single-image media
        #
        # Multi-image posts were already handled above by the
        # playlist UI. A single image does not need a quality
        # selection screen.
        media_type = (
            str(
                media_info.get(
                    "media_type"
                )
                or "video"
            )
            .strip()
            .lower()
        )

        if media_type == "image":

            safe_title = (
                html.escape(
                    str(
                        title
                    )
                )
            )

            await safe_edit_message(
                status_message,
                (
                    "📷 <b>تصویر آماده دانلود است</b>\n\n"
                    f"📌 <b>عنوان:</b> "
                    f"{safe_title}\n"
                    "🖼 <b>کیفیت:</b> "
                    "<code>اصلی</code>\n\n"
                    "⏳ در حال ایجاد درخواست دانلود..."
                ),
            )

            job = (
                await create_download_job(
                    source_url=source_url,
                    quality=None,
                    media_type="image",
                    playlist_index=None,
                )
            )

            job_id = (
                job[
                    "id"
                ]
            )

            await safe_edit_message(
                status_message,
                (
                    "✅ <b>درخواست دانلود تصویر ایجاد شد</b>\n\n"
                    f"🆔 Job ID: "
                    f"<code>{job_id}</code>\n"
                    "🖼 کیفیت: "
                    "<code>اصلی</code>\n"
                    "📊 وضعیت: "
                    "<code>pending</code>"
                ),
                reply_markup=(
                    build_active_download_keyboard(
                        job_id
                    )
                ),
            )

            completed_job = (
                await wait_for_download(
                    job_id,
                    status_message,
                    "تصویر اصلی",
                )
            )

            if (
                completed_job.get(
                    "status"
                )
                == "completed"
            ):

                await send_downloaded_file(
                    message=message,
                    status_message=(
                        status_message
                    ),
                    job=completed_job,
                )

            return

        quality_options = (
            extract_available_quality_options(
                media_info
            )
        )

        quality_options = (
            normalize_quality_options(
                quality_options
            )
        )

        if not quality_options:

            raise RuntimeError(
                "هیچ کیفیت ویدئویی "
                "قابل دانلودی پیدا نشد."
            )

        token = (
            add_pending_selection(
                source_url,
                quality_options,
            )
        )

        keyboard = (
            build_quality_keyboard(
                quality_options=(
                    quality_options
                ),
                token=token,
            )
        )

        safe_title = (
            html.escape(
                str(
                    title
                )
            )
        )

        text = (
            "🎬 <b>ویدئو آماده دانلود است</b>\n\n"

            f"📌 <b>عنوان:</b> "
            f"{safe_title}\n"
        )

        if duration:

            try:

                duration_value = int(
                    duration
                )

            except (
                TypeError,
                ValueError,
            ):

                duration_value = 0

            if (
                duration_value
                > 0
            ):

                hours = (
                    duration_value
                    // 3600
                )

                remaining = (
                    duration_value
                    % 3600
                )

                minutes = (
                    remaining
                    // 60
                )

                seconds = (
                    remaining
                    % 60
                )

                if hours:

                    duration_text = (
                        f"{hours}:"
                        f"{minutes:02d}:"
                        f"{seconds:02d}"
                    )

                else:

                    duration_text = (
                        f"{minutes}:"
                        f"{seconds:02d}"
                    )

                text += (
                    f"⏱ <b>مدت:</b> "
                    f"<code>"
                    f"{duration_text}"
                    f"</code>\n"
                )

        text += (
            "\n🎯 <b>کیفیت موردنظر را انتخاب کنید:</b>\n"
            "📦 حجم‌ها تقریبی هستند."
        )

        await status_message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception as exc:

        print(
            "Media info error: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        safe_error = (
            html.escape(
                str(
                    exc
                )[:1000]
            )
        )

        await safe_edit_message(
            status_message,
            (
                "❌ <b>نتوانستم اطلاعات ویدئو را دریافت کنم</b>\n\n"

                "⚠️ خطا:\n"

                f"<code>"
                f"{safe_error}"
                f"</code>"
            ),
        )


# ============================================================
# Main
# ============================================================

async def main():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    api = (
        TelegramAPIServer.from_base(
            TELEGRAM_BOT_API,
            is_local=True,
        )
    )

    session = (
        AiohttpSession(
            api=api,
            timeout=3600,
        )
    )

    bot = (
        Bot(
            token=TOKEN,
            session=session,
        )
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        await (
            bot.session.close()
        )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
