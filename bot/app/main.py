from app.localization import tr as _tr, localized_collection as _localized_collection
import asyncio
import aiohttp
import html
import ipaddress
import mimetypes
import os
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse


from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import BaseFilter, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Message,
)


from app.keyboards.download import (
    build_active_download_keyboard,
    build_completed_download_keyboard,
    build_paused_download_keyboard,
)


from app.keyboards.media import (
    MEDIA_ENTRY_PAGE_SIZE,
    build_media_entry_keyboard,
)


from app.keyboards.quality import (
    build_quality_keyboard,
)
from app.keyboards.conversion import build_audio_format_keyboard
from app.keyboards.payment import (
    build_home_reply_keyboard,
    build_upgrade_keyboard,
)
from app.handlers.operations import router as operations_router
from app.handlers.conversion import (
    cleanup_staged_state,
    configure_download_runtime,
    router as conversion_router,
)
from app.handlers.home import (
    router as home_router,
)
from app.handlers.experience import (
    enforce_required_membership,
    router as experience_router,
)
from app.handlers.admin import (
    router as admin_router,
)
from app.handlers.admin_settings import (
    router as admin_settings_router,
)
from app.handlers.admin_experience import (
    router as admin_experience_router,
)
from app.handlers.admin_finance import (
    router as admin_finance_router,
)
from app.handlers.admin_statistics import (
    router as admin_statistics_router,
)
from app.handlers.language import (
    router as language_router,
)
from app.handlers.payments import (
    router as payments_router,
)
from app.i18n import home_action_for_text, normalize_language, translate
from app.runtime_config import runtime_configuration, runtime_content
from app.runtime_config import (
    action_for_runtime_text,
    all_runtime_configurations,
)

from app.utils.formatting import (
    format_file_size,
    normalize_quality_label,
)


from app.state.pending import (
    PENDING_MEDIA_ENTRIES,
    PENDING_SELECTIONS,
    add_pending_media_entries,
    add_pending_selection,
)


from app.services.backend import (
    BackendAPIError,
    cancel_download_job,
    create_download_job,
    get_download_job,
    get_download_entitlement,
    get_media_info,
    mark_download_delivered,
    pause_download_job,
    register_telegram_user,
    resume_download_job,
)
from app.utils.media_conversion import AUDIO_FORMATS, normalize_format


TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_BOT_API = os.getenv(
    "TELEGRAM_BOT_API",
    "http://telegram-api:8081",
)

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://redis:6379/0",
)

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

dp = Dispatcher(
    storage=RedisStorage.from_url(
        REDIS_URL
    )
)
from app.middleware.interface import InterfaceContext, InterfaceCallbacks, InterfaceRequests, ui_language
from app.middleware.health import PollHeartbeatMiddleware

dp.message.outer_middleware(InterfaceContext())
dp.callback_query.outer_middleware(InterfaceContext())
dp.callback_query.outer_middleware(InterfaceCallbacks(dp.storage.redis))

dp.include_router(operations_router)
dp.include_router(conversion_router)
dp.include_router(
    experience_router
)
# Admin forms must receive their input before home-menu label matching.
# An edited title can itself be a valid home button, such as "خرید اشتراک".
dp.include_router(
    admin_settings_router
)
dp.include_router(
    admin_experience_router
)
dp.include_router(
    admin_finance_router
)
dp.include_router(
    admin_statistics_router
)
dp.include_router(
    admin_router
)
dp.include_router(
    home_router
)
dp.include_router(
    payments_router
)
dp.include_router(
    language_router
)


# ============================================================
# Pending selections
# ============================================================

# ============================================================
# URL helpers
# ============================================================

class DownloadMessageFilter(
    BaseFilter
):
    """Keep Telegram commands out of the generic download handler."""

    async def __call__(
        self,
        message: Message,
    ) -> bool:

        text = (
            message.text
            or ""
        )

        stripped = text.strip()
        if not stripped or stripped.startswith("/"):
            return False
        if home_action_for_text(stripped) is not None:
            return False

        # Reply-keyboard taps arrive as ordinary text messages.  Runtime
        # labels and custom buttons must be rejected here because this root
        # dispatcher handler is evaluated before child-router handlers.
        if "https://" not in stripped and "http://" not in stripped:
            action = action_for_runtime_text(
                stripped,
                await all_runtime_configurations(),
            )
            if action is not None:
                return False

        return True


def download_error_text(exc: Exception) -> str:
    if not isinstance(exc, BackendAPIError) or not isinstance(exc.detail, dict):
        return str(exc)[:1000]

    code = str(exc.detail.get("code") or "")
    plan_name = str(exc.detail.get("plan_name") or _tr("پلن فعلی"))
    from app.middleware.interface import ui_language
    if ui_language.get() == "en" and any("\u0600" <= ch <= "\u06ff" for ch in plan_name):
        plan_name = "your current plan"
    if plan_name.strip().lower() == "free":
        plan_name = _tr("رایگان")

    if code == "weekly_download_limit_reached":
        if ui_language.get() == "en":
            return f"Your weekly quota for {plan_name} has been reached ({exc.detail.get('used', 0)}/{exc.detail.get('limit', 0)})."
        return f"{_tr('سهمیه هفتگی ')}{plan_name}{_tr(' تمام شده است (')}{exc.detail.get('used', 0)}/{exc.detail.get('limit', 0)})."
    if code == "weekly_conversion_limit_reached":
        if ui_language.get() == "en":
            return (
                "Your Free plan includes one file conversion per week. "
                "Upgrade to Professional or Gold for more conversions."
            )
        return (
            "سهمیهٔ ۱ تبدیل فرمت هفتگی پلن رایگان شما تمام شده است؛ "
            "برای تبدیل بیشتر، پلن حرفه‌ای یا طلایی را تهیه کنید."
        )
    if code == "daily_download_limit_reached":
        return (
            f"{_tr('سهمیه روزانه ')}{plan_name}{_tr(' تمام شده است (')}{exc.detail.get('used', 0)}/{exc.detail.get('limit', 0)})."
        )
    if code == "concurrent_download_limit_reached":
        return (
            f"{_tr('تعداد دانلودهای هم\u200cزمان ')}{plan_name}{_tr(' به سقف ')}{exc.detail.get('limit', 1)}{_tr(' رسیده است.')}"
        )
    if code == "download_quality_limit_exceeded":
        return (
            f"{_tr('حداکثر کیفیت مجاز در ')}{plan_name}{_tr('، ')}{exc.detail.get('max_quality', 0)}{_tr('p است.')}"
        )
    if code == "download_file_size_limit_exceeded":
        return (
            f"{_tr('حداکثر حجم مجاز در ')}{plan_name}{_tr('، ')}{exc.detail.get('max_file_size_mb', 0)}{_tr(' مگابایت است.')}"
        )
    if code == "download_user_blocked":
        return _tr("حساب شما اجازه ایجاد دانلود جدید ندارد.")
    if code == "download_user_not_found":
        return _tr("ابتدا دستور /start را بفرستید و دوباره تلاش کنید.")
    if code == "download_plan_unavailable":
        return _tr("پلن دانلود در حال حاضر در دسترس نیست؛ با پشتیبانی تماس بگیرید.")
    if code == "maintenance_mode":
        return _tr("🛠 ربات موقتاً در حالت تعمیرات است؛ کمی بعد تلاش کنید.")
    if code == "downloads_disabled":
        return _tr("⏸ دریافت لینک و دانلود جدید موقتاً غیرفعال است.")
    if code == "download_temporarily_unavailable":
        reason_code = str(exc.detail.get("reason_code") or "")
        if reason_code == "maintenance_mode":
            return _tr("🛠 ربات موقتاً در حالت تعمیرات است؛ کمی بعد تلاش کنید.")
        return _tr("⏸ دریافت دانلود جدید موقتاً غیرفعال است.")

    return str(exc.detail.get("message") or exc)[:1000]


def download_error_markup(exc: Exception):
    if not isinstance(exc, BackendAPIError) or not isinstance(exc.detail, dict):
        return None

    if str(exc.detail.get("code") or "") in {
        "daily_download_limit_reached",
        "weekly_download_limit_reached",
        "weekly_conversion_limit_reached",
        "download_quality_limit_exceeded",
        "download_file_size_limit_exceeded",
    }:
        from app.middleware.interface import ui_language
        return build_upgrade_keyboard(ui_language.get())
    return None


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
            _tr("ویدئو")
        )

    return (
        original_title
    )

# ============================================================
# Quality helpers
# ============================================================

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

        seconds = int(
            seconds
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    # Zero means either finished or unavailable.
    # Never show "0 seconds remaining".
    if seconds <= 0:

        return None

    if seconds < 60:

        return (
            f"{seconds}{_tr(' ثانیه')}"
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
            f"{hours}{_tr(' ساعت')}"
        )

    if minutes:

        parts.append(
            f"{minutes}{_tr(' دقیقه')}"
        )

    if (
        secs
        and not hours
    ):

        parts.append(
            f"{secs}{_tr(' ثانیه')}"
        )

    if not parts:

        return None

    return (
        _tr(" و ").join(
            parts
        )
    )


# ============================================================
# Progress text
# ============================================================


def _quality_height(
    quality: str,
) -> int | None:

    match = re.search(
        r"(\d{3,4})p",
        str(
            quality
            or ""
        ).lower(),
    )

    if not match:

        return None

    try:

        return int(
            match.group(
                1
            )
        )

    except ValueError:

        return None


def _media_fps_for_quality(
    quality: str,
    media_info: dict | None,
) -> float | None:

    if not isinstance(
        media_info,
        dict,
    ):

        return None

    requested_height = _quality_height(
        quality
    )

    if requested_height is None:

        return None

    candidates: list[
        float
    ] = []

    for item in (
        media_info.get(
            "formats"
        )
        or []
    ):

        if not isinstance(
            item,
            dict,
        ) or not item.get(
            "has_video"
        ):

            continue

        item_height = _get_format_quality(
            item
        )

        if item_height != requested_height:

            continue

        try:

            fps = float(
                item.get(
                    "fps"
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        if fps > 0:

            candidates.append(
                fps
            )

    return max(
        candidates,
        default=None,
    )


def _quality_label_with_fps(
    quality: str,
    media_info: dict | None = None,
) -> str:

    label = str(
        quality
        or _tr("نامشخص")
    )

    fps = _media_fps_for_quality(
        label,
        media_info,
    )

    if fps is None:

        return label

    fps_label = (
        str(
            int(
                fps
            )
        )
        if fps.is_integer()
        else f"{fps:.2f}".rstrip(
            "0"
        ).rstrip(
            "."
        )
    )

    return (
        f"{label} • {fps_label} FPS"
    )


def _progress_bar(
    progress: int,
    width: int = 10,
) -> str:

    safe_progress = max(
        0,
        min(
            int(
                progress
            ),
            100,
        ),
    )

    filled = min(
        width,
        max(
            0,
            int(
                safe_progress * width / 100
            ),
        ),
    )

    return (
        "█" * filled
        + "░" * (
            width - filled
        )
    )


def build_progress_text(
    job_id: int,
    quality: str,
    job: dict,
    paused: bool = False,
    media_info: dict | None = None,
) -> str:

    # ``job_id`` remains in the function signature for existing callers,
    # but deliberately never appears in user-facing progress text.  It is
    # exposed only by the completed-file Details control.
    del job_id

    try:

        progress = int(
            job.get(
                "progress",
                0,
            )
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):

        progress = 0

    progress = max(
        0,
        min(
            progress,
            100,
        ),
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

    # If Backend/Worker has no useful ETA, derive one from
    # remaining bytes and current transfer speed.
    if (
        not paused
        and progress < 100
    ):

        try:

            normalized_eta = (
                float(
                    eta
                )
                if eta is not None
                else None
            )

        except (
            TypeError,
            ValueError,
        ):

            normalized_eta = None

        if (
            normalized_eta
            is None
            or normalized_eta <= 0
        ):

            try:

                normalized_downloaded = int(
                    downloaded_bytes
                    or 0
                )

                normalized_total = int(
                    total_bytes
                    or 0
                )

                normalized_speed = float(
                    speed
                    or 0
                )

                if (
                    normalized_total
                    > normalized_downloaded
                    and normalized_speed > 0
                ):

                    eta = max(
                        1,
                        int(
                            (
                                normalized_total
                                - normalized_downloaded
                            )
                            / normalized_speed
                        ),
                    )

                else:

                    eta = None

            except (
                TypeError,
                ValueError,
            ):

                eta = None

    eta_label = (
        format_eta(
            eta
        )
    )

    english = ui_language.get() == "en"
    quality_label = html.escape(
        _quality_label_with_fps(
            quality,
            media_info,
        )
    )

    lines = [
        (
            "⏸ <b>Download paused</b>"
            if paused
            else "⬇️ <b>Downloading...</b>"
        )
        if english
        else (
            "⏸ <b>دانلود متوقف شده است</b>"
            if paused
            else "⬇️ <b>در حال دانلود...</b>"
        ),
        "",
        (
            f"🎞 Quality: <b>{quality_label}</b>"
            if english
            else f"🎞 کیفیت: <b>{quality_label}</b>"
        ),
        (
            f"📊 Progress: {_progress_bar(progress)} {progress}%"
            if english
            else f"📊 پیشرفت: {_progress_bar(progress)} {progress}%"
        ),
    ]

    if (
        downloaded_label
        and total_label
    ):

        try:

            download_complete = (
                int(
                    downloaded_bytes
                    or 0
                )
                >= int(
                    total_bytes
                    or 0
                )
                > 0
            )

        except (
            TypeError,
            ValueError,
        ):

            download_complete = False

        if download_complete:

            lines.append(
                (
                    f"📦 Size: <b>{downloaded_label}</b>"
                    if english
                    else f"📦 حجم: <b>{downloaded_label}</b>"
                )
            )

        else:

            lines.append(
                (
                    f"📦 Size: <b>{downloaded_label} / {total_label}</b>"
                    if english
                    else f"📦 حجم: <b>{downloaded_label} / {total_label}</b>"
                )
            )

    elif downloaded_label:

        lines.append(
            (
                f"📦 Size: <b>{downloaded_label}</b>"
                if english
                else f"📦 حجم: <b>{downloaded_label}</b>"
            )
        )

    elif total_label:

        lines.append(
            (
                f"📦 Size: <b>— / {total_label}</b>"
                if english
                else f"📦 حجم: <b>— / {total_label}</b>"
            )
        )

    if not paused:

        if speed_label:

            lines.append(
                (
                    f"🚀 Speed: <b>{speed_label}</b>"
                    if english
                    else f"🚀 سرعت: <b>{speed_label}</b>"
                )
            )

        if eta_label:

            lines.append(
                (
                    f"⏱ Remaining: ~<b>{eta_label}</b>"
                    if english
                    else f"⏱ باقی\u200cمانده: ~<b>{eta_label}</b>"
                )
            )

    else:

        lines.extend(
            [
                "",
                (
                    "🕕 The partial file is retained for <b>6 hours</b>."
                    if english
                    else "🕕 فایل نیمه‌کاره تا <b>۶ ساعت</b> نگهداری می‌شود."
                ),
                (
                    "It is then removed to free server space."
                    if english
                    else "پس از آن برای آزادسازی فضای سرور حذف خواهد شد."
                ),
            ]
        )

    return (
        "\n".join(
            lines
        )
    )


def _format_media_duration(
    value: object,
) -> str | None:

    try:

        seconds = int(
            float(
                value
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if seconds <= 0:

        return None

    hours, remainder = divmod(
        seconds,
        3600,
    )
    minutes, seconds = divmod(
        remainder,
        60,
    )

    if hours:

        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


def _source_label(
    source_url: object,
) -> str:

    try:

        hostname = (
            urlparse(
                str(
                    source_url
                    or ""
                )
            ).hostname
            or ""
        ).lower()

    except Exception:

        hostname = ""

    known_sources = (
        ("instagram", "Instagram"),
        ("youtu", "YouTube"),
        ("tiktok", "TikTok"),
        ("twitter", "X"),
        ("x.com", "X"),
        ("facebook", "Facebook"),
        ("fb.watch", "Facebook"),
        ("pinterest", "Pinterest"),
        ("threads", "Threads"),
        ("vimeo", "Vimeo"),
        ("dailymotion", "Dailymotion"),
    )

    for marker, label in known_sources:

        if marker in hostname:

            return label

    if hostname.startswith(
        "www."
    ):

        hostname = hostname[4:]

    return hostname or (
        "Unknown"
        if ui_language.get() == "en"
        else "نامشخص"
    )


def _completed_file_caption(
    filename: str,
    size_label: str,
    job: dict,
    media_info: dict | None = None,
) -> str:

    english = ui_language.get() == "en"
    quality = str(
        job.get(
            "quality"
        )
        or ""
    )

    if not quality:

        output_format = str(
            job.get(
                "output_format"
            )
            or ""
        ).upper()

        media_type = str(
            job.get(
                "media_type"
            )
            or ""
        ).lower()

        if media_type == "audio" and output_format:

            quality = f"audio/{output_format}"

        elif media_type == "convert" and output_format:

            quality = (
                f"Convert to {output_format}"
                if english
                else f"تبدیل به {output_format}"
            )

        else:

            quality = (
                "Original"
                if english
                else "اصلی"
            )

    format_label = (
        Path(
            filename
        ).suffix.lstrip(
            "."
        ).upper()
        or str(
            job.get(
                "output_format"
            )
            or ""
        ).upper()
        or "FILE"
    )

    lines = [
        f"📥 <b>{html.escape(filename)}</b>",
        "",
        (
            "✅ Download completed successfully"
            if english
            else "✅ دانلود با موفقیت انجام شد"
        ),
        "",
        (
            f"📦 Size: <b>{size_label}</b>"
            if english
            else f"📦 حجم: <b>{size_label}</b>"
        ),
        (
            f"🎞 Quality: <b>{html.escape(_quality_label_with_fps(quality, media_info))}</b>"
            if english
            else f"🎞 کیفیت: <b>{html.escape(_quality_label_with_fps(quality, media_info))}</b>"
        ),
    ]

    duration = _format_media_duration(
        (
            media_info
            or {}
        ).get(
            "duration"
        )
    )

    if duration:

        lines.append(
            (
                f"⏱ Duration: <b>{duration}</b>"
                if english
                else f"⏱ مدت: <b>{duration}</b>"
            )
        )

    lines.extend(
        [
            (
                f"📁 Format: <b>{format_label}</b>"
                if english
                else f"📁 فرمت: <b>{format_label}</b>"
            ),
            (
                f"🔗 Source: <b>{html.escape(_source_label(job.get('source_url')))}</b>"
                if english
                else f"🔗 منبع: <b>{html.escape(_source_label(job.get('source_url')))}</b>"
            ),
        ]
    )

    return "\n".join(
        lines
    )


def _queued_download_text(
    quality: str,
    media_info: dict | None = None,
) -> str:

    quality_label = html.escape(
        _quality_label_with_fps(
            quality,
            media_info,
        )
    )

    if ui_language.get() == "en":

        return (
            "⏳ <b>Download queued</b>\n\n"
            f"🎞 Quality: <b>{quality_label}</b>\n"
            "📊 Status: <b>pending</b>"
        )

    return (
        "⏳ <b>درخواست در صف دانلود است</b>\n\n"
        f"🎞 کیفیت: <b>{quality_label}</b>\n"
        "📊 وضعیت: <b>pending</b>"
    )


def _download_details_text(
    job: dict,
) -> str:

    job_id = str(
        job.get(
            "id"
        )
        or "—"
    )
    quality = str(
        job.get(
            "quality"
        )
        or job.get(
            "output_format"
        )
        or (
            "Unknown"
            if ui_language.get() == "en"
            else "نامشخص"
        )
    )
    status = str(
        job.get(
            "status"
        )
        or "—"
    )
    format_label = str(
        job.get(
            "output_format"
        )
        or Path(
            str(
                job.get(
                    "file_path"
                )
                or ""
            )
        ).suffix.lstrip(
            "."
        )
        or "—"
    ).upper()

    if ui_language.get() == "en":

        return (
            "📋 <b>Download details</b>\n\n"
            f"🆔 Job ID: <code>{html.escape(job_id)}</code>\n"
            f"📊 Status: <b>{html.escape(status)}</b>\n"
            f"🎞 Quality: <b>{html.escape(quality)}</b>\n"
            f"📁 Format: <b>{html.escape(format_label)}</b>\n"
            f"🔗 Source: <b>{html.escape(_source_label(job.get('source_url')))}</b>"
        )

    return (
        "📋 <b>جزئیات دانلود</b>\n\n"
        f"🆔 Job ID: <code>{html.escape(job_id)}</code>\n"
        f"📊 وضعیت: <b>{html.escape(status)}</b>\n"
        f"🎞 کیفیت: <b>{html.escape(quality)}</b>\n"
        f"📁 فرمت: <b>{html.escape(format_label)}</b>\n"
        f"🔗 منبع: <b>{html.escape(_source_label(job.get('source_url')))}</b>"
    )


# ============================================================
# Pending selection
# ============================================================

# ============================================================
# Backend - user
# ============================================================

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

# ============================================================
# Quality keyboard
# ============================================================

def build_smaller_quality_keyboard(
    source_url: str,
    selected_height: int,
    sizes: dict[
        int,
        int | None,
    ],
    playlist_index: int | None = None,
    media_info: dict | None = None,
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
            media_info=media_info,
        )
    )

    return (
        build_quality_keyboard(
            quality_options=(
                quality_options
            ),
            token=token,
            audio_token=token,
            cover_token=token,
            description_token=token,
            language=ui_language.get(),
        )
    )

# ============================================================
# Wait for download
# ============================================================

async def wait_for_download(
    job_id: int,
    message: Message,
    quality: str,
    media_info: dict | None = None,
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
                    _queued_download_text(
                        quality,
                        media_info,
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
                        media_info=media_info,
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
                        media_info=media_info,
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
                    (
                        f"\n📦 Downloaded before cancellation: <b>{downloaded_label}</b>"
                        if ui_language.get() == "en"
                        else f"\n📦 دانلود شده تا زمان لغو: <b>{downloaded_label}</b>"
                    )
                )

            await safe_edit_message(
                message,
                (
                    "❌ <b>Download cancelled</b>\n\n"
                    f"📊 Progress at cancellation: <b>{progress}%</b>"
                    f"{extra}\n\n🗑 Temporary files were removed from the server."
                    if ui_language.get() == "en"
                    else "❌ <b>دانلود لغو شد</b>\n\n"
                    f"📊 پیشرفت هنگام لغو: <b>{progress}%</b>"
                    f"{extra}\n\n🗑 فایل‌های موقت از سرور حذف شدند."
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
                    "⌛ <b>Download expired</b>\n\n"
                    "The download was paused for more than 6 hours.\n"
                    "🗑 The partial file was removed to free server space."
                    if ui_language.get() == "en"
                    else "⌛ <b>دانلود منقضی شد</b>\n\n"
                    "بیش از ۶ ساعت از توقف دانلود گذشته بود.\n"
                    "🗑 فایل موقت برای آزادسازی فضای سرور حذف شد."
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
                _tr("خطای نامشخص")
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
    media_info: dict | None = None,
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
            "✅ <b>Download complete</b>\n\n"
            f"📦 File size: <b>{size_label}</b>\n\n"
            "📤 <b>Sending file to Telegram...</b>"
            if ui_language.get() == "en"
            else "✅ <b>دانلود کامل شد</b>\n\n"
            f"📦 حجم فایل: <b>{size_label}</b>\n\n"
            "📤 <b>در حال ارسال فایل به تلگرام...</b>"
        ),
        reply_markup=None,
    )

    suffix = (
        path.suffix
        or ".mp4"
    )

    filename = (
        f"MediaHub-"
        f"{job_id}"
        f"{suffix}"
    )

    document = (
        FSInputFile(
            path=str(
                path
            ),
            filename=filename,
        )
    )

    try:

        await message.answer_document(
            document=document,
            disable_content_type_detection=True,
            caption=_completed_file_caption(
                filename=filename,
                size_label=size_label,
                job=job,
                media_info=media_info,
            ),
            parse_mode="HTML",
            reply_markup=build_completed_download_keyboard(
                job_id
            ),
        )

        delivery_confirmed = False
        for attempt in range(3):
            try:
                await mark_download_delivered(job_id)
                delivery_confirmed = True
                break
            except Exception as exc:
                print(
                    "Delivery confirmation failed: "
                    f"job={job_id}, attempt={attempt + 1}, "
                    f"{type(exc).__name__}: {exc}"
                )
                if attempt < 2:
                    await asyncio.sleep(1)

        if not delivery_confirmed:
            print(
                "Delivery confirmation requires reconciliation: "
                f"job={job_id}"
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


# Conversion callbacks share the ordinary download progress and delivery
# implementation.  Register it here rather than importing this entrypoint
# from a router while it is already executing as ``__main__``.
configure_download_runtime(
    wait_for_download=wait_for_download,
    send_downloaded_file=send_downloaded_file,
    download_error_text=download_error_text,
    download_error_markup=download_error_markup,
)


# ============================================================
# Start
# ============================================================

@dp.message(
    CommandStart()
)
async def start_handler(
    message: Message,
    state: FSMContext,
):

    await cleanup_staged_state(state)
    try:

        user = await register_telegram_user(
            message
        )

        language = user.get(
            "effective_language",
            normalize_language(message.from_user.language_code),
        )

        telegram_id = (
            message.from_user.id
        )
        configuration = await runtime_configuration(language)

        await cleanup_staged_state(state)
        await state.clear()

        await message.answer(
            (
                f"{html.escape(runtime_content(configuration, 'welcome_title'))}\n\n"
                f"{html.escape(runtime_content(configuration, 'welcome_instruction'))}\n\n"
                f"{translate(language, 'start.telegram_id', telegram_id=telegram_id)}"
            ),
            parse_mode="HTML",
            reply_markup=(
                build_home_reply_keyboard(
                    language=language,
                    include_admin=(
                        bool(user.get("is_admin"))
                        and message.chat.type == "private"
                    ),
                    configuration=configuration,
                )
            ),
        )

    except Exception as exc:

        print(
            "User registration failed: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        await message.answer(
            translate(
                normalize_language(
                    message.from_user.language_code
                    if message.from_user
                    else None
                ),
                "start.registration_error",
            ),
            parse_mode="HTML",
            # Keep the menu recoverable even when registration temporarily
            # fails. Telegram keeps this keyboard persistent for the chat.
            reply_markup=build_home_reply_keyboard(
                normalize_language(
                    message.from_user.language_code
                    if message.from_user
                    else None
                )
            ),
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
            _tr("❌ Job نامعتبر است."),
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
            _tr("⏸ دانلود متوقف شد.")
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
                _tr("نامشخص")
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
            f"❌ {download_error_text(exc)[:150]}",
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
            _tr("❌ Job نامعتبر است."),
            show_alert=True,
        )

        return

    try:

        job = (
            await resume_download_job(
                job_id
            )
        )

        quality = str(
            job.get(
                "quality"
            )
            or
            _tr("نامشخص")
        )

        await callback.answer(
            _tr("▶️ دانلود ادامه پیدا کرد.")
        )

        if isinstance(
            callback.message,
            Message,
        ):

            await safe_edit_message(
                callback.message,
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

    except Exception as exc:

        await callback.answer(
            f"❌ {download_error_text(exc)[:150]}",
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
            _tr("❌ Job نامعتبر است."),
            show_alert=True,
        )

        return

    try:

        job = (
            await cancel_download_job(
                job_id
            )
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
                (
                    f"\n📦 Downloaded before cancellation: <b>{downloaded_label}</b>"
                    if ui_language.get() == "en"
                    else f"\n📦 دانلود شده تا زمان لغو: <b>{downloaded_label}</b>"
                )
            )

        await callback.answer(
            _tr("❌ دانلود لغو شد.")
        )

        if isinstance(
            callback.message,
            Message,
        ):

            await safe_edit_message(
                callback.message,
                (
                    "❌ <b>Download cancelled</b>"
                    if ui_language.get() == "en"
                    else "❌ <b>دانلود لغو شد</b>"
                )
                + extra
                + (
                    "\n\n🗑 The temporary file will be removed."
                    if ui_language.get() == "en"
                    else "\n\n🗑 فایل موقت از سرور حذف می‌شود."
                ),
                reply_markup=None,
            )

    except Exception as exc:

        await callback.answer(
            f"❌ {download_error_text(exc)[:150]}",
            show_alert=True,
        )


# ============================================================
# Delivered-file actions
# ============================================================

@dp.callback_query(F.data.startswith("download_details:"))
async def download_details_callback(
    callback: CallbackQuery,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
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
            _tr("❌ Job نامعتبر است."),
            show_alert=True,
        )
        return

    try:

        job = await get_download_job(
            job_id
        )

    except Exception as exc:

        await callback.answer(
            f"❌ {download_error_text(exc)[:150]}",
            show_alert=True,
        )
        return

    await callback.answer(
        "Details sent." if ui_language.get() == "en" else "جزئیات ارسال شد."
    )
    await callback.message.answer(
        _download_details_text(
            job
        ),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("download_again:"))
async def download_again_callback(
    callback: CallbackQuery,
) -> None:
    if (
        not callback.data
        or not isinstance(
            callback.message,
            Message,
        )
        or not callback.from_user
    ):
        return

    try:

        original_job_id = int(
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
            _tr("❌ Job نامعتبر است."),
            show_alert=True,
        )
        return

    language = ui_language.get()

    try:

        original = await get_download_job(
            original_job_id
        )

        if str(
            original.get(
                "status"
            )
            or ""
        ) != "completed":

            await callback.answer(
                "This download is not ready to repeat."
                if language == "en"
                else "این دانلود هنوز برای تکرار آماده نیست.",
                show_alert=True,
            )
            return

        source_url = str(
            original.get(
                "source_url"
            )
            or ""
        )
        media_type = str(
            original.get(
                "media_type"
            )
            or "video"
        ).lower()

        if not source_url:

            raise RuntimeError(
                "Original source URL is unavailable"
            )

        if media_type == "convert":

            await callback.answer(
                "Upload the original file again to convert it again."
                if language == "en"
                else "برای تبدیل دوباره، فایل اصلی را دوباره ارسال کنید.",
                show_alert=True,
            )
            return

        await callback.answer(
            "Starting the download again…"
            if language == "en"
            else "دانلود مجدد شروع شد…"
        )

        media_info: dict = {}

        try:

            refreshed_info = await get_media_info(
                source_url=source_url,
                playlist_index=original.get(
                    "playlist_index"
                ),
            )

            if isinstance(
                refreshed_info,
                dict,
            ):

                media_info = refreshed_info

        except Exception as exc:

            print(
                "Could not refresh redownload metadata: "
                f"{type(exc).__name__}: {exc}"
            )

        quality = str(
            original.get(
                "quality"
            )
            or (
                f"audio/{str(original.get('output_format') or '').upper()}"
                if media_type == "audio"
                else (
                    "Original"
                    if language == "en"
                    else "اصلی"
                )
            )
        )
        status_message = await callback.message.answer(
            (
                "⏳ <b>Preparing your download…</b>"
                if language == "en"
                else "⏳ <b>در حال آماده‌سازی دانلود…</b>"
            ),
            parse_mode="HTML",
        )
        job = await create_download_job(
            source_url=source_url,
            telegram_id=callback.from_user.id,
            format_id=original.get("format_id"),
            quality=original.get("quality"),
            media_type=media_type,
            output_format=original.get("output_format"),
            playlist_index=original.get("playlist_index"),
            estimated_size_bytes=original.get("total_bytes"),
        )
        job_id = int(
            job[
                "id"
            ]
        )
        await safe_edit_message(
            status_message,
            _queued_download_text(
                quality,
                media_info,
            ),
            reply_markup=build_active_download_keyboard(
                job_id
            ),
        )
        completed = await wait_for_download(
            job_id,
            status_message,
            quality,
            media_info,
        )

        if completed.get("status") == "completed":

            await send_downloaded_file(
                message=status_message,
                status_message=status_message,
                job=completed,
                media_info=media_info,
            )

            try:

                await status_message.delete()

            except Exception as exc:

                print(
                    "Redownload status cleanup failed: "
                    f"{type(exc).__name__}: {exc}"
                )

    except Exception as exc:

        print(
            "Redownload failed: "
            f"{type(exc).__name__}: {exc}"
        )
        await callback.message.answer(
            (
                "❌ The download could not be started again.\n\n"
                if language == "en"
                else "❌ دانلود مجدد شروع نشد.\n\n"
            )
            + html.escape(
                download_error_text(
                    exc
                )[:800]
            ),
            parse_mode="HTML",
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
            _tr("❌ درخواست نامعتبر است."),
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
                _tr("⌛ این انتخاب منقضی شده است. "
                "لینک را دوباره ارسال کنید.")
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
            _tr("❌ شماره صفحه نامعتبر است."),
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
            _tr("❌ این صفحه وجود ندارد."),
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
            f"{_tr('صفحه ')}{page + 1}{_tr(' از ')}{total_pages}"
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
            _tr("❌ درخواست نامعتبر است."),
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
                _tr("⌛ این انتخاب منقضی شده است. "
                "لینک را دوباره ارسال کنید.")
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
            _tr("❌ شماره رسانه نامعتبر است."),
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
            _tr("❌ لینک رسانه پیدا نشد."),
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
            _tr("❌ رسانه انتخاب‌شده پیدا نشد."),
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
            f"{_tr('📷 عکس ')}{index}{_tr(' انتخاب شد.')}"
        )

        try:

            image_media_info = {
                "title": selected_entry.get(
                    "title"
                ),
                "description": selected_entry.get(
                    "description"
                ),
                "duration": selected_entry.get(
                    "duration"
                ),
                "thumbnail": selected_entry.get(
                    "thumbnail"
                ),
                "formats": [],
            }

            await safe_edit_message(
                message,
                (
                    f"{_tr('⏳ <b>در حال ایجاد درخواست دانلود عکس...</b>\n\n📷 شماره عکس: <code>')}{index}{_tr('</code>\n🖼 کیفیت: <code>اصلی</code>')}"
                ),
            )

            job = (
                await create_download_job(
                    source_url=source_url,
                    telegram_id=callback.from_user.id,
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
                _queued_download_text(
                    _tr("تصویر اصلی"),
                    image_media_info,
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
                    _tr("تصویر اصلی"),
                    image_media_info,
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
                media_info=image_media_info,
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
                    f"{_tr('❌ <b>دانلود عکس با خطا مواجه شد</b>\n\n⚠️ خطا:\n<code>')}{html.escape(download_error_text(exc))}</code>"
                ),
                reply_markup=download_error_markup(exc),
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
        f"{_tr('🎬 ویدئو ')}{index}{_tr(' انتخاب شد.')}"
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
                _tr("هیچ کیفیت ویدئویی "
                "قابل دانلودی پیدا نشد.")
            )

        token = (
            add_pending_selection(
                source_url,
                quality_options,
                playlist_index=index,
                media_info=selected_info,
            )
        )

        keyboard = (
            build_quality_keyboard(
                quality_options=(
                    quality_options
                ),
                token=token,
                audio_token=token,
                cover_token=token,
                description_token=token,
                language=ui_language.get(),
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
                f"{_tr('🎬 <b>ویدئو ')}{index}{_tr(' آماده دانلود است</b>\n\n📌 <b>عنوان:</b> ')}{safe_title}{_tr('\n\n🎯 <b>کیفیت موردنظر را انتخاب کنید:</b>\n📦 حجم\u200cها تقریبی هستند.')}"
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
                f"{_tr('❌ <b>دریافت اطلاعات رسانه ناموفق بود</b>\n\n⚠️ خطا:\n<code>')}{html.escape(str(exc)[:1000])}</code>"
            ),
            reply_markup=None,
        )


# ============================================================
# Quality callback
# ============================================================

def _audio_picker_keyboard(token: str, language: str) -> InlineKeyboardMarkup:
    return build_audio_format_keyboard(
        token,
        language=language,
        callback_prefix="audio:format",
        cancel_callback=f"audio:cancel:{token}",
    )


MAX_COVER_SIZE_BYTES = 50 * 1024 * 1024


def _cover_extension(
    url: str,
    content_type: str | None,
) -> str:

    try:

        suffix = Path(
            urlparse(
                url
            ).path
        ).suffix.lower()

    except Exception:

        suffix = ""

    if suffix in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }:

        return suffix

    guessed = mimetypes.guess_extension(
        (
            content_type
            or ""
        ).split(
            ";",
            1,
        )[0]
        .strip()
        .lower()
    )

    if guessed in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }:

        return guessed

    return ".jpg"


async def _ensure_public_cover_url(
    value: str,
) -> str:
    """Reject localhost/private cover URLs before the Bot fetches them."""

    parsed = urlparse(
        value
    )

    if (
        parsed.scheme not in {
            "http",
            "https",
        }
        or not parsed.hostname
    ):

        raise RuntimeError(
            "Cover image URL is invalid"
        )

    hostname = parsed.hostname.lower().rstrip(
        "."
    )

    if hostname in {
        "localhost",
        "localhost.localdomain",
    }:

        raise RuntimeError(
            "Cover image host is not public"
        )

    port = (
        parsed.port
        or (
            443
            if parsed.scheme == "https"
            else 80
        )
    )

    try:

        resolved = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )

    except OSError as exc:

        raise RuntimeError(
            "Cover image host could not be resolved"
        ) from exc

    if not resolved:

        raise RuntimeError(
            "Cover image host could not be resolved"
        )

    for entry in resolved:

        try:

            address = ipaddress.ip_address(
                entry[4][0]
            )

        except (
            IndexError,
            ValueError,
        ) as exc:

            raise RuntimeError(
                "Cover image host is invalid"
            ) from exc

        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):

            raise RuntimeError(
                "Cover image host is not public"
            )

    return value


async def _download_cover_file(
    cover_url: str,
    token: str,
) -> Path:
    """Fetch one public cover image to the shared download volume.

    The URL is obtained from the extractor, while the callback token is kept
    server-side.  The function streams to disk, so a large image never sits
    entirely in Bot memory.
    """

    timeout = aiohttp.ClientTimeout(
        total=90,
        connect=20,
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; MediaHubAI/1.0)"
        ),
    }

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers,
    ) as session:

        current_url = cover_url

        for _redirect in range(4):

            await _ensure_public_cover_url(
                current_url
            )

            async with session.get(
                current_url,
                allow_redirects=False,
            ) as response:

                if 300 <= response.status < 400:

                    location = response.headers.get(
                        "Location"
                    )

                    if not location:

                        raise RuntimeError(
                            "Cover redirect has no destination"
                        )

                    current_url = urljoin(
                        current_url,
                        location,
                    )
                    continue

                if response.status != 200:

                    raise RuntimeError(
                        f"Cover download returned HTTP {response.status}"
                    )

                content_length = response.content_length

                if (
                    content_length is not None
                    and content_length > MAX_COVER_SIZE_BYTES
                ):

                    raise RuntimeError(
                        "Cover image is too large"
                    )

                suffix = _cover_extension(
                    str(
                        response.url
                    ),
                    response.headers.get(
                        "Content-Type"
                    ),
                )
                target = DOWNLOAD_DIR / (
                    f"cover-{token}{suffix}"
                )
                received = 0

                try:

                    with target.open(
                        "wb"
                    ) as destination:

                        async for chunk in response.content.iter_chunked(
                            64 * 1024
                        ):

                            received += len(
                                chunk
                            )

                            if received > MAX_COVER_SIZE_BYTES:

                                raise RuntimeError(
                                    "Cover image is too large"
                                )

                            destination.write(
                                chunk
                            )

                except Exception:

                    target.unlink(
                        missing_ok=True
                    )
                    raise

                if received <= 0:

                    target.unlink(
                        missing_ok=True
                    )
                    raise RuntimeError(
                        "Cover image is empty"
                    )

                return target

    raise RuntimeError(
        "Cover image exceeded redirect limit"
    )


async def _selection_media_info(
    selection: dict,
) -> dict:
    """Refresh metadata when possible, retaining the original menu state."""

    cached = selection.get(
        "media_info"
    )
    result = (
        dict(
            cached
        )
        if isinstance(
            cached,
            dict,
        )
        else {}
    )
    source_url = str(
        selection.get(
            "source_url"
        )
        or ""
    )

    if not source_url:

        return result

    try:

        refreshed = await get_media_info(
            source_url=source_url,
            playlist_index=selection.get(
                "playlist_index"
            ),
        )

    except Exception as exc:

        print(
            "Could not refresh media metadata: "
            f"{type(exc).__name__}: {exc}"
        )
        return result

    if isinstance(
        refreshed,
        dict,
    ):

        return refreshed

    return result


def _description_chunks(
    description: str,
    maximum_length: int = 3400,
) -> list[str]:

    value = description.strip()

    if not value:

        return []

    chunks: list[
        str
    ] = []

    while len(
        value
    ) > maximum_length:

        split_at = value.rfind(
            "\n",
            0,
            maximum_length,
        )

        if split_at < maximum_length // 2:

            split_at = value.rfind(
                " ",
                0,
                maximum_length,
            )

        if split_at < maximum_length // 2:

            split_at = maximum_length

        chunks.append(
            value[:split_at].rstrip()
        )
        value = value[split_at:].lstrip()

    if value:

        chunks.append(
            value
        )

    return chunks


@dp.callback_query(F.data.startswith("media:cover:"))
async def media_cover_callback(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    token = callback.data.split(":", 2)[-1]
    selection = PENDING_SELECTIONS.get(token)

    if not selection:
        await callback.answer(
            _tr("⏰ این درخواست منقضی شده است. لطفاً لینک را دوباره ارسال کنید."),
            show_alert=True,
        )
        return

    language = ui_language.get()
    await callback.answer(
        "Downloading the highest-quality cover…"
        if language == "en"
        else "در حال دریافت کاور با بالاترین کیفیت…"
    )

    try:

        media_info = await _selection_media_info(
            selection
        )
        cover_url = str(
            media_info.get(
                "thumbnail"
            )
            or ""
        ).strip()

        if not cover_url:

            raise RuntimeError(
                "No cover image is available for this media"
            )

        cover_path = await _download_cover_file(
            cover_url,
            token,
        )
        title = str(
            media_info.get(
                "title"
            )
            or _source_label(
                selection.get(
                    "source_url"
                )
            )
        ).strip()
        caption = (
            "🖼 <b>Cover — highest available quality</b>\n\n"
            f"📌 <b>Title:</b> {html.escape(title[:700])}"
            if language == "en"
            else "🖼 <b>کاور با بالاترین کیفیت موجود</b>\n\n"
            f"📌 <b>عنوان:</b> {html.escape(title[:700])}"
        )

        try:

            await callback.message.answer_photo(
                photo=FSInputFile(
                    str(
                        cover_path
                    ),
                    filename=(
                        f"MediaHub-cover{cover_path.suffix.lower() or '.jpg'}"
                    ),
                ),
                caption=caption,
                parse_mode="HTML",
            )

        except Exception:

            await callback.message.answer_document(
                document=FSInputFile(
                    str(
                        cover_path
                    ),
                    filename=(
                        f"MediaHub-cover{cover_path.suffix.lower() or '.jpg'}"
                    ),
                ),
                caption=caption,
                parse_mode="HTML",
            )

        finally:

            cover_path.unlink(
                missing_ok=True
            )

    except Exception as exc:

        print(
            "Cover extraction failed: "
            f"{type(exc).__name__}: {exc}"
        )
        await callback.message.answer(
            (
                "❌ I could not download this media cover."
                if language == "en"
                else "❌ دریافت کاور این رسانه انجام نشد."
            )
        )


@dp.callback_query(F.data.startswith("media:description:"))
async def media_description_callback(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return

    token = callback.data.split(":", 2)[-1]
    selection = PENDING_SELECTIONS.get(token)

    if not selection:
        await callback.answer(
            _tr("⏰ این درخواست منقضی شده است. لطفاً لینک را دوباره ارسال کنید."),
            show_alert=True,
        )
        return

    language = ui_language.get()
    await callback.answer(
        "Loading description…"
        if language == "en"
        else "در حال دریافت توضیحات…"
    )

    media_info = await _selection_media_info(
        selection
    )
    description = str(
        media_info.get(
            "description"
        )
        or ""
    ).strip()

    if not description:

        await callback.message.answer(
            (
                "ℹ️ No description was provided for this media."
                if language == "en"
                else "ℹ️ برای این رسانه توضیحی در دسترس نیست."
            )
        )
        return

    chunks = _description_chunks(
        description
    )
    header = (
        "📝 <b>Media description</b>\n\n"
        if language == "en"
        else "📝 <b>توضیحات رسانه</b>\n\n"
    )

    for index, chunk in enumerate(chunks):

        prefix = (
            header
            if index == 0
            else (
                "📝 <b>Description (continued)</b>\n\n"
                if language == "en"
                else "📝 <b>ادامهٔ توضیحات</b>\n\n"
            )
        )

        await callback.message.answer(
            prefix + html.escape(
                chunk
            ),
            parse_mode="HTML",
        )


@dp.callback_query(F.data.startswith("audio:open:"))
async def audio_open_callback(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    token = callback.data.split(":", 2)[-1]
    selection = PENDING_SELECTIONS.get(token)
    if not selection:
        await callback.answer(
            _tr("⏰ این درخواست منقضی شده است. لطفاً لینک را دوباره ارسال کنید."),
            show_alert=True,
        )
        return

    language = ui_language.get()
    await callback.answer(
        "Choose an audio format." if language == "en" else "فرمت صوتی را انتخاب کنید."
    )
    await safe_edit_message(
        callback.message,
        (
            "🎵 <b>Extract audio</b>\n\nChoose the output format:"
            if language == "en"
            else "🎵 <b>استخراج صدا</b>\n\nفرمت خروجی را انتخاب کنید:"
        ),
        reply_markup=_audio_picker_keyboard(token, language),
    )


@dp.callback_query(F.data.startswith("audio:cancel:"))
async def audio_cancel_callback(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    token = callback.data.split(":", 2)[-1]
    selection = PENDING_SELECTIONS.get(token)
    if not selection:
        await callback.answer(_tr("⏰ این درخواست منقضی شده است."), show_alert=True)
        return
    quality_options = [
        (int(height), size)
        for height, size in (selection.get("sizes") or {}).items()
        if str(height).isdigit()
    ]
    language = ui_language.get()
    await callback.answer(
        "Back to quality selection." if language == "en" else "بازگشت به انتخاب کیفیت."
    )
    await safe_edit_message(
        callback.message,
        (
            "🎬 <b>Choose video quality</b>"
            if language == "en"
            else "🎬 <b>کیفیت ویدئو را انتخاب کنید</b>"
        ),
        reply_markup=build_quality_keyboard(
            quality_options=quality_options,
            token=token,
            audio_token=token,
            cover_token=token,
            description_token=token,
            language=language,
        ),
    )


@dp.callback_query(F.data.startswith("audio:format:"))
async def audio_format_callback(callback: CallbackQuery) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Invalid audio request.", show_alert=True)
        return
    _, _, token, raw_format = parts
    output_format = normalize_format(raw_format)
    if output_format not in AUDIO_FORMATS:
        await callback.answer("Unsupported audio format.", show_alert=True)
        return
    selection = PENDING_SELECTIONS.pop(token, None)
    if not selection:
        await callback.answer(_tr("⏰ این درخواست منقضی شده است."), show_alert=True)
        return

    source_url = str(selection.get("source_url") or "")
    playlist_index = selection.get("playlist_index")
    media_info = (
        selection.get(
            "media_info"
        )
        if isinstance(
            selection.get(
                "media_info"
            ),
            dict,
        )
        else {}
    )
    if not source_url:
        await callback.answer("The media link is missing.", show_alert=True)
        return

    language = ui_language.get()
    status_message = callback.message
    quality_label = f"audio/{output_format.upper()}"
    try:
        await callback.answer(
            f"Extracting {output_format.upper()}…"
            if language == "en"
            else f"در حال استخراج {output_format.upper()}…"
        )
        await safe_edit_message(
            status_message,
            (
                f"⏳ <b>Creating audio job…</b>\n\n🎵 Output: <code>{output_format.upper()}</code>"
                if language == "en"
                else f"⏳ <b>در حال ایجاد درخواست صوتی…</b>\n\n🎵 خروجی: <code>{output_format.upper()}</code>"
            ),
        )
        job = await create_download_job(
            source_url=source_url,
            telegram_id=callback.from_user.id,
            quality=None,
            media_type="audio",
            output_format=output_format,
            playlist_index=playlist_index,
        )
        job_id = int(job["id"])
        await safe_edit_message(
            status_message,
            _queued_download_text(
                quality_label,
                media_info,
            ),
            reply_markup=build_active_download_keyboard(job_id),
        )
        completed_job = await wait_for_download(
            job_id,
            status_message,
            quality_label,
            media_info,
        )
        if completed_job.get("status") == "completed":
            await send_downloaded_file(
                message=status_message,
                status_message=status_message,
                job=completed_job,
                media_info=media_info,
            )
    except asyncio.TimeoutError:
        await safe_edit_message(
            status_message,
            "⌛ Audio job timed out. Check its status later."
            if language == "en"
            else "⌛ زمان انتظار استخراج صدا تمام شد؛ وضعیت درخواست را بعداً بررسی کنید.",
            reply_markup=None,
        )
    except Exception as exc:
        await safe_edit_message(
            status_message,
            (
                "❌ <b>Audio extraction failed</b>\n\n"
                if language == "en"
                else "❌ <b>استخراج صدا انجام نشد</b>\n\n"
            ) + html.escape(download_error_text(exc)[:1000]),
            reply_markup=download_error_markup(exc),
        )

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
            _tr("❌ درخواست نامعتبر است."),
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
            _tr("❌ درخواست نامعتبر است."),
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
                _tr("⏰ این درخواست منقضی شده است. "
                "لطفاً لینک را دوباره ارسال کنید.")
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

    media_info = (
        selection.get(
            "media_info"
        )
        if isinstance(
            selection.get(
                "media_info"
            ),
            dict,
        )
        else {}
    )

    if not source_url:

        await callback.answer(
            _tr("❌ لینک دانلود پیدا نشد."),
            show_alert=True,
        )

        return

    try:

        height = int(
            height_text
        )

    except ValueError:

        await callback.answer(
            _tr("❌ کیفیت نامعتبر است."),
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
            _tr("نامشخص")
        )

        await callback.answer(
            (
                _tr("حجم این کیفیت "
                "بیش از حد مجاز است.")
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
                media_info=media_info,
            )
        )

        text = (
            f"{_tr('⚠️ <b>حجم این کیفیت بیش از حد مجاز است</b>\n\n🎬 کیفیت انتخاب\u200cشده: <code>')}{quality}{_tr('</code>\n📦 حجم تقریبی: <code>')}{size_label}</code>\n\n"
        )

        if smaller_keyboard:

            text += (
                _tr("👇 لطفاً یکی از کیفیت‌های "
                "پایین‌تر را انتخاب کنید.")
            )

        else:

            text += (
                _tr("❌ کیفیت پایین‌تری در "
                "محدوده مجاز پیدا نشد.")
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
            f"{_tr('کیفیت ')}{quality}{_tr(' انتخاب شد.')}"
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
                    f"{_tr('\n📦 حجم تقریبی: <code>')}{estimated_label}</code>"
                )

        await safe_edit_message(
            status_message,
            (
                f"{_tr('⏳ <b>در حال ایجاد درخواست دانلود...</b>\n\n🎬 کیفیت: <code>')}{quality}</code>{estimated_text}"
            ),
        )

        job = (
            await create_download_job(
                source_url=source_url,
                telegram_id=callback.from_user.id,
                quality=quality,
                playlist_index=(
                    playlist_index
                ),
                estimated_size_bytes=estimated_size,
            )
        )

        job_id = (
            job[
                "id"
            ]
        )

        await safe_edit_message(
            status_message,
            _queued_download_text(
                quality,
                media_info,
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
                media_info,
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
            media_info=media_info,
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
                _tr("⌛ <b>زمان انتظار ربات به پایان رسید</b>\n\n"

                "وضعیت Job را دوباره بررسی کنید.")
            ),
            reply_markup=None,
        )

    except Exception as exc:

        print(
            "Download error: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        raw_error_text = (
            str(
                exc
            )[:1000]
        )

        error_text = download_error_text(exc)

        if (
            is_youtube_url(
                source_url
            )
            and height > 360
            and "403"
            in raw_error_text
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
                    playlist_index=playlist_index,
                    media_info=media_info,
                )
            )

            fallback_keyboard = build_quality_keyboard(
                quality_options=fallback_options,
                token=fallback_token,
                audio_token=fallback_token,
                cover_token=fallback_token,
                description_token=fallback_token,
                language=ui_language.get(),
            )

            await safe_edit_message(
                status_message,
                (
                    f"{_tr('❌ <b>کیفیت ')}{quality}{_tr(' فعلاً از YouTube قابل دریافت نیست</b>\n\n✅ کیفیت <code>360p</code> در دسترس است.')}"
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
                f"{_tr('❌ <b>دانلود انجام نشد</b>\n\n⚠️ خطا:\n<code>')}{safe_error}</code>"
            ),
            reply_markup=None,
        )


# ============================================================
# URL handler
# ============================================================

@dp.message(
    StateFilter(None),
    F.text,
    DownloadMessageFilter(),
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

    if not message.from_user:
        return

    try:
        user = await register_telegram_user(message)
    except Exception as exc:
        print(
            "Download user registration failed: "
            f"{type(exc).__name__}: {exc}"
        )
        await message.answer(
            _tr("❌ ثبت حساب کاربری انجام نشد؛ چند لحظه بعد دوباره تلاش کنید.")
        )
        return

    try:
        entitlement = await get_download_entitlement(message.from_user.id)
        if entitlement.get("forced_join_required"):
            language = normalize_language(user.get("effective_language"))
            configuration = await runtime_configuration(language)
            if not await enforce_required_membership(
                message,
                telegram_id=message.from_user.id,
                configuration=configuration,
            ):
                return
    except BackendAPIError as exc:
        await message.answer(download_error_text(exc), reply_markup=download_error_markup(exc))
        return

    status_message = (
        await message.answer(
            (
                _tr("🔎 <b>لینک دریافت شد</b>\n"

                "در حال بررسی کیفیت‌های موجود...")
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

        single_playlist_index: (
            int
            | None
        ) = None

        if (
            len(
                entries
            )
            == 1
        ):

            try:

                single_playlist_index = int(
                    entries[0].get(
                        "index"
                    )
                )

            except (
                TypeError,
                ValueError,
                AttributeError,
            ):

                single_playlist_index = None

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
                    f"{_tr('📚 <b>این پست شامل ')}{len(entries)}{_tr(' رسانه است</b>\n\n📌 <b>عنوان:</b> ')}{safe_title}{_tr('\n\n👇 <b>رسانه موردنظر را انتخاب کنید:</b>')}"
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
                    f"{_tr('📷 <b>تصویر آماده دانلود است</b>\n\n📌 <b>عنوان:</b> ')}{safe_title}{_tr('\n🖼 <b>کیفیت:</b> <code>اصلی</code>\n\n⏳ در حال ایجاد درخواست دانلود...')}"
                ),
            )

            job = (
                await create_download_job(
                    source_url=source_url,
                    telegram_id=message.from_user.id,
                    quality=None,
                    media_type="image",
                    playlist_index=(
                        single_playlist_index
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
                _queued_download_text(
                    _tr("تصویر اصلی"),
                    media_info,
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
                    _tr("تصویر اصلی"),
                    media_info,
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
                    media_info=media_info,
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
                _tr("هیچ کیفیت ویدئویی "
                "قابل دانلودی پیدا نشد.")
            )

        token = (
            add_pending_selection(
                source_url,
                quality_options,
                playlist_index=(
                    single_playlist_index
                ),
                media_info=media_info,
            )
        )

        keyboard = (
            build_quality_keyboard(
                quality_options=(
                    quality_options
                ),
                token=token,
                audio_token=token,
                cover_token=token,
                description_token=token,
                language=ui_language.get(),
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
            f"{_tr('🎬 <b>ویدئو آماده دانلود است</b>\n\n📌 <b>عنوان:</b> ')}{safe_title}\n"
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
                    f"{_tr('⏱ <b>مدت:</b> <code>')}{duration_text}</code>\n"
                )

        text += (
            _tr("\n🎯 <b>کیفیت موردنظر را انتخاب کنید:</b>\n"
            "📦 حجم‌ها تقریبی هستند.")
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
                download_error_text(exc)
            )
        )

        await safe_edit_message(
            status_message,
            (
                f"{_tr('❌ <b>نتوانستم اطلاعات ویدئو را دریافت کنم</b>\n\n⚠️ خطا:\n<code>')}{safe_error}</code>"
            ),
            reply_markup=download_error_markup(exc),
        )


# ============================================================
# Main
# ============================================================

async def configure_telegram_menu_button(bot: Bot) -> None:
    """Expose /menu from Telegram's supported input-area menu button.

    Existing commands configured for the bot are retained.  A transient Bot
    API failure must never prevent the polling process from starting.
    """
    try:
        commands = await bot.get_my_commands()
        if not any(command.command == "menu" for command in commands):
            await bot.set_my_commands(
                [
                    *commands,
                    BotCommand(
                        command="menu",
                        description="Open main menu / باز کردن منوی اصلی",
                    ),
                ]
            )
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:
        print(
            "Could not configure the Telegram menu button: "
            f"{type(exc).__name__}: {exc}"
        )

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

    session.middleware(PollHeartbeatMiddleware(dp.storage.redis))
    session.middleware(InterfaceRequests(dp.storage.redis))

    bot = (
        Bot(
            token=TOKEN,
            session=session,
        )
    )

    try:

        # Telegram owns the area beside Attach, so a bot cannot put an
        # arbitrary ReplyKeyboard button there. Its supported equivalent is
        # the command-menu button: /menu restores the persistent bottom menu
        # after a user has manually collapsed it.
        await configure_telegram_menu_button(bot)

        await dp.start_polling(
            bot
        )

    finally:

        await (
            dp.storage.close()
        )

        await (
            bot.session.close()
        )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
