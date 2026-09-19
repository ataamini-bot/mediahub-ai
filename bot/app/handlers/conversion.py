"""Telegram upload, audio extraction, and media conversion flow."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import aiofiles
from aiogram import Bot, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.keyboards.conversion import build_conversion_format_keyboard
from app.keyboards.download import build_active_download_keyboard
from app.middleware.interface import ui_language
from app.runtime_config import runtime_configuration
from app.services.backend import (
    BackendAPIError,
    create_download_job,
    get_download_entitlement,
    register_telegram_user,
)
from app.state.conversion import ConversionStates
from app.utils.media_conversion import (
    SUPPORTED_FORMATS,
    available_output_formats,
    normalize_format,
    probe_media_file,
)


router = Router(name="conversion")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


logger = logging.getLogger(__name__)


DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/app/downloads"))
MAX_UPLOAD_BYTES = 1900 * 1024 * 1024
UPLOAD_PREFIX = "upload://"
LOCAL_BOT_API_DATA_DIR = Path(
    os.getenv("TELEGRAM_BOT_API_DATA_DIR", "/var/lib/telegram-bot-api")
)


def _positive_int_setting(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


LOCAL_FILE_READ_ATTEMPTS = _positive_int_setting(
    "TELEGRAM_LOCAL_FILE_READ_ATTEMPTS",
    12,
)
LOCAL_FILE_READ_DELAY_SECONDS = 0.5


# The conversion router is included by ``app.main``.  Importing ``app.main``
# from a callback would execute the module for a second time when the Bot is
# launched with ``python -m app.main``; aiogram then rejects the already
# attached routers.  Main registers the runtime functions below after it has
# defined them, so this router never imports the module that owns Dispatcher.
DownloadRuntimeFunction = Callable[..., Any]
_download_runtime: dict[str, DownloadRuntimeFunction] = {}


def configure_download_runtime(
    *,
    wait_for_download: Callable[[int, Message, str], Awaitable[dict]],
    send_downloaded_file: Callable[..., Awaitable[None]],
    download_error_text: Callable[[Exception], str],
    download_error_markup: Callable[[Exception], InlineKeyboardMarkup | None],
) -> None:
    """Register the shared delivery helpers owned by the Bot entrypoint."""

    _download_runtime.update(
        {
            "wait_for_download": wait_for_download,
            "send_downloaded_file": send_downloaded_file,
            "download_error_text": download_error_text,
            "download_error_markup": download_error_markup,
        }
    )


def _download_runtime_function(name: str) -> DownloadRuntimeFunction:
    function = _download_runtime.get(name)
    if not callable(function):
        raise RuntimeError("Conversion delivery runtime is not configured")
    return function


def _is_english() -> bool:
    return ui_language.get() == "en"


def _cancel_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="❌ Cancel" if language == "en" else "❌ انصراف",
            callback_data="convert:cancel",
        )
    ]])


def _attachment(message: Message) -> tuple[object, str, int | None] | None:
    """Return every Telegram attachment type that can contain media.

    Telegram uses different fields for a regular video, a round video,
    voice message, music upload, animation, and a file sent as a document.
    All are staged through the same reader and then verified with ffprobe.
    """

    candidates = (
        (message.video, "video.mp4"),
        (message.animation, "animation.mp4"),
        (message.audio, "audio"),
        (message.voice, "voice.ogg"),
        (message.video_note, "video.mp4"),
        (message.document, "media"),
    )
    for item, fallback_name in candidates:
        if item is None or not getattr(item, "file_id", None):
            continue
        name = str(getattr(item, "file_name", None) or fallback_name)
        size = getattr(item, "file_size", None)
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        return item, name, size
    return None


def _safe_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if len(suffix) > 10 or not suffix.startswith("."):
        return ".bin"
    suffix_value = suffix[1:]
    if suffix_value and all(char in "abcdefghijklmnopqrstuvwxyz0123456789" for char in suffix_value):
        return suffix
    return ".bin"


def _local_file_candidates(bot: object, file_path: str) -> tuple[Path, ...]:
    """Return paths that can represent a Local Bot API file in this container.

    Local Bot API returns an absolute ``file_path`` from ``getFile``.  The
    normal aiogram downloader handles this path, but an explicit candidate
    list lets us support deployments where the server and Bot use equivalent
    volume paths or where a custom ``FilesPathWrapper`` is configured.
    """

    api = getattr(getattr(bot, "session", None), "api", None)
    candidates: list[Path] = []
    raw_path = str(file_path or "").strip()
    if not raw_path:
        return ()

    parsed = urlparse(raw_path)
    if parsed.scheme == "file":
        # A Local Bot API may return either ``file:///...`` or a file URI
        # containing its own hostname.  The mounted path is still the URI
        # path; the hostname is not a directory inside the shared volume.
        raw_path = unquote(parsed.path)

    wrapper = getattr(api, "wrap_local_file", None)
    if wrapper is not None:
        try:
            wrapped = wrapper.to_local(raw_path)
        except (TypeError, ValueError, OSError):
            wrapped = None
        if wrapped:
            candidates.append(Path(str(wrapped)))

    raw = Path(raw_path)
    candidates.append(raw)
    if not raw.is_absolute():
        candidates.append(LOCAL_BOT_API_DATA_DIR / raw)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def _copy_local_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        return
    shutil.copyfile(source, destination)


def _file_is_complete(path: Path, expected_size: int | None = None) -> bool:
    """Return whether a staged file is non-empty and, when known, complete."""

    try:
        size = path.stat().st_size if path.is_file() else 0
    except OSError:
        return False
    if size <= 0:
        return False
    return expected_size is None or size >= expected_size


def _expected_size(value: object) -> int | None:
    try:
        size = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return size if size is not None and size > 0 else None


async def _copy_local_telegram_file(
    bot: object,
    file_path: str,
    destination: Path,
    *,
    expected_size: int | None = None,
) -> bool:
    """Copy a Local Bot API file after it becomes visible in the shared volume.

    The local server can report a file path a moment before the corresponding
    file is visible to another container.  Retrying the shared, read-only
    volume avoids treating that short propagation window as a failed upload.
    """

    candidates = _local_file_candidates(bot, file_path)
    for attempt in range(LOCAL_FILE_READ_ATTEMPTS):
        for candidate in candidates:
            try:
                if not _file_is_complete(candidate, expected_size):
                    continue
                await asyncio.to_thread(_copy_local_file, candidate, destination)
                if _file_is_complete(destination, expected_size):
                    return True
                destination.unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Local Telegram file copy failed for %s: %s",
                    candidate,
                    exc,
                )
                destination.unlink(missing_ok=True)
        if attempt + 1 < LOCAL_FILE_READ_ATTEMPTS:
            await asyncio.sleep(LOCAL_FILE_READ_DELAY_SECONDS)
    return False


async def _download_via_file_endpoint(
    bot: object,
    file_path: str,
    destination: Path,
    *,
    timeout: int = 3600,
) -> None:
    """Stream a file through the configured Bot API endpoint.

    This is a fallback for Local Bot API installations where the absolute
    path returned by ``getFile`` is not mounted at the same path in the Bot
    container.  It also keeps the normal remote Bot API path working.
    """

    session = getattr(bot, "session", None)
    api = getattr(session, "api", None)
    if session is None or api is None or not hasattr(session, "stream_content"):
        raise RuntimeError("Telegram file endpoint is unavailable")

    token = str(getattr(bot, "token", "") or "")
    if not token:
        raise RuntimeError("Telegram bot token is unavailable")
    url = api.file_url(token, file_path)
    stream = session.stream_content(
        url=url,
        timeout=timeout,
        chunk_size=1024 * 1024,
        raise_for_status=True,
    )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(destination, "wb") as output:
            async for chunk in stream:
                await output.write(chunk)
    finally:
        close_stream = getattr(stream, "aclose", None)
        if callable(close_stream):
            await close_stream()


async def _download_via_telegram_cloud(
    bot: object,
    file_id: str,
    destination: Path,
    *,
    timeout: int = 3600,
) -> None:
    """Use Telegram's public file endpoint only as a Local API fallback.

    A normal Local Bot API upload is read from the shared volume first, which
    preserves the Local API's large-file support.  This fallback is useful if
    a small upload is unavailable from that volume due to a container-specific
    path issue.  It talks only to Telegram's official Bot API with the bot's
    existing token and never exposes that token in messages or logs.
    """

    token = str(getattr(bot, "token", "") or "")
    if not token:
        raise RuntimeError("Telegram bot token is unavailable")

    cloud_session = AiohttpSession(
        api=TelegramAPIServer.from_base("https://api.telegram.org"),
        timeout=timeout,
    )
    cloud_bot = Bot(token=token, session=cloud_session)
    try:
        cloud_file = await cloud_bot.get_file(file_id)
        cloud_path = str(getattr(cloud_file, "file_path", "") or "").strip()
        if not cloud_path:
            raise RuntimeError("Telegram cloud did not return a file path")
        await cloud_bot.download_file(
            cloud_path,
            destination=destination,
            timeout=timeout,
        )
    finally:
        await cloud_session.close()


async def _download_telegram_file(
    bot: object,
    file_id: str,
    destination: Path,
    *,
    timeout: int = 3600,
    expected_size: int | None = None,
) -> str:
    """Download an incoming Telegram media file into ``destination``.

    Returns a short source label for diagnostics.  Local files are copied
    directly when possible; the Bot API endpoint is used as a safe fallback.
    """

    file_info = await bot.get_file(file_id)
    file_path = str(getattr(file_info, "file_path", "") or "").strip()
    if not file_path:
        raise RuntimeError("Telegram did not return a file path")

    api = getattr(getattr(bot, "session", None), "api", None)
    is_local = bool(getattr(api, "is_local", False))
    expected_size = _expected_size(expected_size)
    if expected_size is None:
        expected_size = _expected_size(getattr(file_info, "file_size", None))

    if is_local and await _copy_local_telegram_file(
        bot,
        file_path,
        destination,
        expected_size=expected_size,
    ):
        return "local-path"

    # ``download_file`` preserves aiogram's normal behavior for remote Bot
    # API servers.  In Local Bot API mode it merely tries the same host path
    # already retried above, so proceed directly to the two network fallbacks.
    download_file = getattr(bot, "download_file", None)
    if not is_local and callable(download_file):
        try:
            await download_file(file_path, destination=destination, timeout=timeout)
            if _file_is_complete(destination, expected_size):
                return "download-file"
            destination.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Telegram download_file failed for %s: %s", file_path, exc)
            destination.unlink(missing_ok=True)

    try:
        await _download_via_file_endpoint(
            bot,
            file_path,
            destination,
            timeout=min(timeout, 90),
        )
        if _file_is_complete(destination, expected_size):
            return "file-endpoint"
        destination.unlink(missing_ok=True)
        raise RuntimeError("Telegram file endpoint returned an incomplete file")
    except Exception as endpoint_error:
        destination.unlink(missing_ok=True)
        if not is_local:
            raise RuntimeError("Telegram file endpoint download failed") from endpoint_error
        logger.warning(
            "Local Telegram file endpoint fallback failed for %s: %s",
            file_path,
            type(endpoint_error).__name__,
        )

    try:
        await _download_via_telegram_cloud(
            bot,
            file_id,
            destination,
            timeout=min(timeout, 90),
        )
        if _file_is_complete(destination, expected_size):
            return "telegram-cloud"
        destination.unlink(missing_ok=True)
        raise RuntimeError("Telegram cloud returned an incomplete file")
    except Exception as cloud_error:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            "Telegram media download failed through local and cloud fallbacks"
        ) from cloud_error


def cleanup_staged_upload(data: dict | None) -> None:
    """Remove an unqueued conversion upload when the user leaves the flow."""

    if not isinstance(data, dict):
        return
    name = str(data.get("input_name") or "")
    if (
        not name
        or not name.startswith("upload-")
        or Path(name).name != name
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in name)
    ):
        return
    try:
        (DOWNLOAD_DIR / "incoming" / name).unlink(missing_ok=True)
    except OSError:
        pass


async def cleanup_staged_state(state: FSMContext) -> None:
    getter = getattr(state, "get_data", None)
    if callable(getter):
        cleanup_staged_upload(await getter())


async def send_conversion_prompt(message: Message, state: FSMContext) -> None:
    language = ui_language.get()
    await cleanup_staged_state(state)
    await state.clear()
    await state.set_state(ConversionStates.waiting_for_media)
    text = (
        "🔄 <b>Convert media</b>\n\n"
        "Send an audio or video file; then choose the output format.\n\n"
        "Free: one conversion per week. Professional and Gold: each conversion uses one daily output."
        if language == "en"
        else "🔄 <b>تبدیل فایل</b>\n\n"
        "یک فایل صوتی یا ویدئویی ارسال کنید؛ سپس فرمت خروجی را انتخاب کنید.\n\n"
        "رایگان: ۱ تبدیل در هر هفته. حرفه‌ای و طلایی: هر تبدیل یک خروجی از سهمیه روزانه را مصرف می‌کند."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_cancel_keyboard(language))


@router.callback_query(F.data == "convert:open")
async def open_conversion(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        return
    await send_conversion_prompt(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "convert:cancel")
async def cancel_conversion(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cleanup_staged_upload(data)
    await state.clear()
    language = ui_language.get()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Conversion cancelled." if language == "en" else "تبدیل فایل لغو شد."
        )
    await callback.answer("Cancelled." if language == "en" else "لغو شد.")


@router.message(ConversionStates.waiting_for_media)
async def receive_conversion_media(message: Message, state: FSMContext) -> None:
    language = ui_language.get()
    attachment = _attachment(message)
    if attachment is None:
        await message.answer(
            "Send an audio/video file or press Cancel."
            if language == "en"
            else "یک فایل صوتی/ویدئویی ارسال کنید یا انصراف را بزنید."
        )
        return

    item, original_name, declared_size = attachment
    if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
        await message.answer(
            "This file is larger than the 1900 MB limit."
            if language == "en"
            else "حجم فایل بیشتر از سقف ۱۹۰۰ مگابایت است."
        )
        return

    try:
        await register_telegram_user(message)
        entitlement = await get_download_entitlement(message.from_user.id)
        if entitlement.get("forced_join_required"):
            from app.handlers.experience import enforce_required_membership

            configuration = await runtime_configuration(language)
            if not await enforce_required_membership(
                message,
                telegram_id=message.from_user.id,
                configuration=configuration,
            ):
                return
    except BackendAPIError as exc:
        await message.answer(str(exc.detail)[:1000])
        return
    except Exception:
        await message.answer(
            "Could not prepare the conversion. Try again later."
            if language == "en"
            else "آماده‌سازی تبدیل انجام نشد؛ کمی بعد دوباره تلاش کنید."
        )
        return

    incoming_dir = DOWNLOAD_DIR / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"upload-{uuid.uuid4().hex}{_safe_suffix(original_name)}"
    input_path = incoming_dir / stored_name
    try:
        source = await _download_telegram_file(
            message.bot,
            item.file_id,
            input_path,
            timeout=3600,
            expected_size=declared_size,
        )
        logger.info(
            "Conversion upload staged: user=%s name=%s source=%s size=%s",
            message.from_user.id,
            stored_name,
            source,
            input_path.stat().st_size if input_path.is_file() else 0,
        )
        if not input_path.is_file() or input_path.stat().st_size <= 0:
            raise RuntimeError("Telegram returned an empty file")
        file_size = input_path.stat().st_size
        if file_size > MAX_UPLOAD_BYTES:
            raise ValueError("The uploaded file exceeds the 1900 MB limit")

        probe = probe_media_file(input_path)
        if probe is None:
            raise ValueError("The file does not contain a readable audio/video stream")
        formats = available_output_formats(
            has_audio=bool(probe.get("has_audio")),
            has_video=bool(probe.get("has_video")),
        )
        if not formats:
            raise ValueError("No audio/video stream was found")

        token = uuid.uuid4().hex[:12]
        await state.update_data(
            conversion_token=token,
            input_name=stored_name,
            source_ref=f"{UPLOAD_PREFIX}{stored_name}",
            input_size=file_size,
            has_audio=bool(probe.get("has_audio")),
            has_video=bool(probe.get("has_video")),
            original_name=original_name,
        )
        await state.set_state(ConversionStates.choosing_format)
        title = (
            f"🎞 <b>{html.escape(original_name[:120])}</b>\n\n"
            "Choose the target format:"
            if language == "en"
            else f"🎞 <b>{html.escape(original_name[:120])}</b>\n\nفرمت خروجی را انتخاب کنید:"
        )
        await message.answer(
            title,
            parse_mode="HTML",
            reply_markup=build_conversion_format_keyboard(
                token,
                has_audio=bool(probe.get("has_audio")),
                has_video=bool(probe.get("has_video")),
                language=language,
            ),
        )
    except ValueError as exc:
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass
        await message.answer(str(exc) if language == "en" else "این فایل قابل تبدیل نیست.")
    except Exception as exc:
        logger.exception(
            "Conversion upload could not be read: user=%s file_id=%s name=%s",
            message.from_user.id,
            getattr(item, "file_id", ""),
            original_name,
        )
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass
        await message.answer(
            "Could not read this media file."
            if language == "en"
            else "خواندن این فایل رسانه‌ای انجام نشد."
        )


@router.callback_query(ConversionStates.choosing_format, F.data.startswith("convert:format:"))
async def choose_conversion_format(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Invalid conversion request.", show_alert=True)
        return
    _convert, _format, token, output_format = parts
    data = await state.get_data()
    if token != str(data.get("conversion_token") or ""):
        await callback.answer(
            "This conversion menu expired." if _is_english() else "این منوی تبدیل منقضی شده است.",
            show_alert=True,
        )
        return
    normalized = normalize_format(output_format)
    if normalized is None or normalized not in SUPPORTED_FORMATS:
        await callback.answer("Unsupported output format.", show_alert=True)
        return
    allowed = available_output_formats(
        has_audio=bool(data.get("has_audio")),
        has_video=bool(data.get("has_video")),
    )
    if normalized not in allowed:
        await callback.answer("This format is not valid for the input.", show_alert=True)
        return

    source_ref = str(data.get("source_ref") or "")
    input_name = str(data.get("input_name") or "")
    input_size = int(data.get("input_size") or 0)
    await state.clear()
    status_message = callback.message
    language = ui_language.get()
    job_created = False
    try:
        await callback.answer(
            f"Converting to {normalized.upper()}…" if language == "en" else f"در حال تبدیل به {normalized.upper()}…"
        )
        await status_message.edit_text(
            (
                f"⏳ <b>Creating conversion job…</b>\n\n🎯 Output: <code>{normalized.upper()}</code>"
                if language == "en"
                else f"⏳ <b>در حال ایجاد درخواست تبدیل…</b>\n\n🎯 خروجی: <code>{normalized.upper()}</code>"
            ),
            parse_mode="HTML",
        )
        job = await create_download_job(
            source_url=source_ref,
            telegram_id=callback.from_user.id,
            media_type="convert",
            output_format=normalized,
            estimated_size_bytes=input_size,
        )
        job_created = True
        job_id = int(job["id"])
        await status_message.edit_text(
            (
                f"✅ <b>Conversion queued</b>\n\n🆔 Job ID: <code>{job_id}</code>\n🎯 Output: <code>{normalized.upper()}</code>\n📊 Status: <code>pending</code>"
                if language == "en"
                else f"✅ <b>درخواست تبدیل ایجاد شد</b>\n\n🆔 Job ID: <code>{job_id}</code>\n🎯 خروجی: <code>{normalized.upper()}</code>\n📊 وضعیت: <code>pending</code>"
            ),
            parse_mode="HTML",
            reply_markup=build_active_download_keyboard(job_id),
        )

        wait_for_download = _download_runtime_function("wait_for_download")
        send_downloaded_file = _download_runtime_function("send_downloaded_file")

        completed = await wait_for_download(
            job_id,
            status_message,
            f"{normalized.upper()} conversion",
        )
        if completed.get("status") == "completed":
            await send_downloaded_file(
                message=status_message,
                status_message=status_message,
                job=completed,
            )
    except BackendAPIError as exc:
        # Keep quota errors consistent with ordinary downloads without
        # importing the entrypoint (which would attach routers a second time).
        download_error_markup = _download_runtime_function("download_error_markup")
        download_error_text = _download_runtime_function("download_error_text")

        await status_message.edit_text(
            "❌ Conversion failed.\n\n" + html.escape(download_error_text(exc))
            if language == "en"
            else "❌ تبدیل انجام نشد.\n\n" + html.escape(download_error_text(exc)),
            parse_mode="HTML",
            reply_markup=download_error_markup(exc),
        )
        # A definite Backend rejection did not create a job, so the Bot owns
        # this staged file and can clean it up. Unknown transport errors are
        # handled below without deleting a file a Worker may already own.
        if not job_created and source_ref.startswith(UPLOAD_PREFIX):
            try:
                (DOWNLOAD_DIR / "incoming" / input_name).unlink(missing_ok=True)
            except OSError:
                pass
    except Exception as exc:
        await status_message.edit_text(
            (
                "❌ Conversion failed.\n\n" if language == "en" else "❌ تبدیل انجام نشد.\n\n"
            ) + html.escape(str(exc)[:1000]),
            parse_mode="HTML",
        )
        # If the job was never accepted by Backend, remove the staged file.
        # Once queued, the Worker owns cleanup so a transient Bot polling
        # failure cannot delete its input while it is still processing.
        if (
            not job_created
            and source_ref.startswith(UPLOAD_PREFIX)
            and int(getattr(exc, "status_code", 500) or 500) < 500
        ):
            try:
                (DOWNLOAD_DIR / "incoming" / input_name).unlink(missing_ok=True)
            except OSError:
                pass
