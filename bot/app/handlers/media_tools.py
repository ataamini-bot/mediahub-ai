"""Private music recognition with immediate submission and full-song delivery."""
import asyncio
import html
import logging
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters

from app.handlers.conversion import _attachment, _download_telegram_file, _download_runtime_function, cleanup_staged_state
from app.middleware.interface import ui_language
from app.keyboards.download import build_active_download_keyboard
from app.runtime_config import runtime_configuration
from app.tool_texts import TOOL_TEXTS, tool_text as text
from app.services.backend import BackendAPIError, _payment_request, get_download_entitlement, register_telegram_user
from app.music_presentation import recovery_keyboard, update_music_card
from app.navigation import MediaFormInputFilter

router = Router(name="media_tools")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
ROOT = Path(os.environ.get("DOWNLOAD_DIR", "/app/downloads")) / "tools"
LIMITS = {"music": 25 * 1024**2}
FORMATS = ("mp3", "m4a", "wav", "aac", "flac", "ogg", "opus")
_uploads_in_progress = set()
_song_downloads_in_progress = set()
logger = logging.getLogger(__name__)


class ToolTextContext(BaseMiddleware):
    async def __call__(self, handler, event, data):
        await runtime_configuration(ui_language.get())
        return await handler(event, data)


router.message.middleware(ToolTextContext())
router.callback_query.middleware(ToolTextContext())


class ToolStates(StatesGroup):
    upload = State()
    confirmation = State()
    working = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, callback_data=data) for title, data in row] for row in rows])


def cancel_row(request_id=""):
    return [(text('cancel'), "tool:cancel:" + request_id)]


def tool_folder(telegram_id, request_id):
    path = ROOT / f"{int(telegram_id)}-{UUID(str(request_id)).hex}"
    if ROOT.is_symlink() or path.is_symlink():
        raise ValueError("Unsafe path")
    return path


def cleanup_tool_state(data):
    # After submission the Worker owns cleanup, even if the HTTP response is lost.
    if not data or data.get("tool_queued") or not data.get("tool_request_id"):
        return
    try:
        path = tool_folder(data["tool_owner"], data["tool_request_id"])
        (path / "input").unlink(missing_ok=True)
        path.rmdir()
    except (ValueError, KeyError, OSError):
        pass


def friendly_error(code):
    key = "error_" + str(code)
    return text(key if key in TOOL_TEXTS else "error_tool_failed")


def error_code(exc):
    return exc.detail.get("code") if isinstance(exc.detail, dict) else "tool_failed"


async def access(message, telegram_id):
    entitlement = await get_download_entitlement(telegram_id)
    if entitlement.get("forced_join_required"):
        from app.handlers.experience import enforce_required_membership
        return await enforce_required_membership(message, telegram_id=telegram_id,
            configuration=await runtime_configuration(ui_language.get()))
    return True


async def send_tool_prompt(message, state, kind, telegram_id):
    await runtime_configuration(ui_language.get())
    if kind != "music":
        await message.answer(friendly_error("tool_disabled"))
        return
    await cleanup_staged_state(state)
    await state.clear()
    try:
        config = await _payment_request("GET", f"/media-tools/configuration/{kind}")
        if not config.get("available"):
            await message.answer(friendly_error("tool_disabled"))
            return
        if not await access(message, telegram_id):
            return
    except BackendAPIError as exc:
        await message.answer(friendly_error(error_code(exc)))
        return
    await state.set_state(ToolStates.upload)
    await state.update_data(tool_kind=kind, tool_owner=telegram_id)
    await message.answer(text('b_identify_a_song_and'), parse_mode="HTML", reply_markup=keyboard([cancel_row()]))


@router.callback_query(F.data == "tool:open:music")
async def open_tool(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await register_telegram_user(callback)
    await send_tool_prompt(callback.message, state, callback.data.rsplit(":", 1)[1], callback.from_user.id)


@router.message(ToolStates.upload, F.document | F.audio | F.voice | F.video | F.video_note | F.animation)
async def upload(message: Message, state: FSMContext):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id in _uploads_in_progress:
        await message.answer(text('the_previous_upload_is_still'))
        return
    _uploads_in_progress.add(user_id)
    try:
        await stage_upload(message, state)
    finally:
        _uploads_in_progress.discard(user_id)


@router.message(ToolStates.upload, F.text, MediaFormInputFilter())
async def receive_music_link(message: Message, state: FSMContext):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id in _uploads_in_progress:
        await message.answer(text('the_previous_upload_is_still'))
        return
    _uploads_in_progress.add(user_id)
    try:
        await stage_music_link(message, state)
    finally:
        _uploads_in_progress.discard(user_id)


async def stage_music_link(message, state):
    data = await state.get_data()
    if data.get("tool_kind") != "music" or not message.from_user:
        await message.answer(text('send_one_video_post_link'))
        return
    urls = re.findall(r"https?://[^\s<>\[\]()]+", message.text or "")
    urls += [item.url for item in (message.entities or []) if getattr(item, "url", None)]
    urls = list(dict.fromkeys(url.rstrip(".,،!؟") for url in urls))
    if len(urls) != 1 or len(urls[0]) > 2048:
        await message.answer(text('send_one_video_post_link'))
        return
    cleanup_tool_state(data)
    request_id = uuid4().hex
    await state.update_data(tool_request_id=request_id, tool_owner=message.from_user.id,
                            tool_queued=False, tool_source_url=urls[0])
    await start_staged_tool(message, state, request_id)


async def stage_upload(message, state):
    data = await state.get_data()
    kind = data.get("tool_kind")
    if kind not in LIMITS or not message.from_user:
        return
    attached = _attachment(message)
    item = attached[0] if attached else None
    if item is None:
        await message.answer(text('this_file_type_is_not'))
        return
    size = getattr(item, "file_size", None)
    if not isinstance(size, int) or not 0 < size <= LIMITS[kind]:
        await message.answer(text('the_file_limit_is_limit', limit_mib=LIMITS[kind] // 1024 ** 2))
        return
    request_id = uuid4().hex
    path = tool_folder(message.from_user.id, request_id)
    cleanup_tool_state(data)
    await state.update_data(tool_request_id=request_id, tool_owner=message.from_user.id, tool_queued=False, tool_source_url=None)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        await _download_telegram_file(message.bot, item.file_id, path / "input", expected_size=size, timeout=90)
        if not 0 < (path / "input").stat().st_size <= LIMITS[kind]:
            raise ValueError("File limit exceeded")
    except Exception:
        cleanup_tool_state({"tool_request_id": request_id, "tool_owner": message.from_user.id})
        await message.answer(text('the_upload_was_not_completed'))
        return
    # The user can leave this form while Telegram is still transferring the
    # file. Never overwrite a newer form or delete its upload on completion.
    if (await state.get_data()).get("tool_request_id") != request_id:
        cleanup_tool_state({"tool_request_id": request_id, "tool_owner": message.from_user.id})
        return
    await start_staged_tool(message, state, request_id)


async def valid_state(callback, state, request_id):
    data = await state.get_data()
    if data.get("tool_request_id") != request_id or data.get("tool_owner") != callback.from_user.id or data.get("tool_queued"):
        await callback.answer(text('this_menu_expired'), show_alert=True)
        return None
    return data


async def start_staged_tool(message, state, request_id):
    await state.update_data(tool_input_message_id=getattr(message, "message_id", None))
    await submit_tool(message, message.from_user.id, state, request_id)


def status_keyboard(request_id):
    return keyboard([[(text('check_result'), f"tool:check:{request_id}")], cancel_row(request_id)])


@router.callback_query(F.data.startswith("tool:start:"))
async def start_tool(callback: CallbackQuery, state: FSMContext):
    # Keep old inline buttons usable after an update; new inputs have no prompt.
    request_id = callback.data.rsplit(":", 1)[1]
    try:
        UUID(request_id)
    except ValueError:
        return
    data = await state.get_data()
    if data.get("tool_request_id") != request_id or data.get("tool_owner") != callback.from_user.id:
        await callback.answer(text('this_menu_expired'), show_alert=True)
        return
    await callback.answer()
    await submit_tool(callback.message, callback.from_user.id, state, request_id, edit_message=True)


async def submit_tool(message, telegram_id, state, request_id, *, edit_message=False):
    data = await state.get_data()
    if data.get("tool_request_id") != request_id or data.get("tool_owner") != telegram_id:
        return
    if data.get("tool_kind") != "music":
        cleanup_tool_state(data)
        await state.clear()
        await message.answer(friendly_error("tool_disabled"))
        return
    if not await access(message, telegram_id):
        return
    # Membership checks yield: a cancellation or a newer form must win.
    if (await state.get_data()).get("tool_request_id") != request_id:
        return
    # Mark the handoff before HTTP so a lost response cannot cause cleanup to
    # delete a file that a Worker already accepted.
    await state.update_data(tool_queued=True)
    await state.set_state(ToolStates.working)
    payload = {"request_id": request_id, "telegram_id": telegram_id, "kind": "music"}
    if data["tool_kind"] == "music":
        payload["auto_mp3"] = True
        if data.get("tool_source_url"):
            payload["source_url"] = data["tool_source_url"]
    try:
        await _payment_request("POST", "/media-tools", payload=payload)
    except BackendAPIError as exc:
        code = error_code(exc)
        if code != "backend_unavailable":
            cleanup_tool_state({**data, "tool_queued": False})
            await state.clear()
        await message.answer(friendly_error(code), reply_markup=status_keyboard(request_id) if code == "backend_unavailable" else None)
        return
    if edit_message:
        await message.edit_text(text('request_accepted_processing'), reply_markup=status_keyboard(request_id))
        progress = message
    else:
        progress = await message.answer(text('request_accepted_processing'), reply_markup=status_keyboard(request_id))
    await wait_result(progress, telegram_id, request_id, reply_to=data.get("tool_input_message_id"))
    # Keep the music form ready for another input without overwriting a new menu.
    if (await state.get_data()).get("tool_request_id") == request_id:
        await state.set_state(ToolStates.upload)


async def maybe_show_preview(message, telegram_id, request_id, row, *, reply_to=None):
    if not (row.get("result") or {}).get("preview") or row.get("delivery_state"):
        return message
    claimed = False
    path = tool_folder(telegram_id, request_id) / "preview.mp4"
    try:
        claim = await _payment_request("POST", f"/media-tools/{request_id}/claim-preview",
                                       payload={"telegram_id": telegram_id})
        claimed = bool(claim.get("claimed"))
        if not claimed:
            return message
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size < 5 * 1024**2:
            return message
        preview = claim["preview"]
        sent = await message.answer_video(video=FSInputFile(path, filename="MediaHub-preview.mp4"),
            caption=html.escape(text("music_preview_processing")), parse_mode="HTML", supports_streaming=True,
            duration=preview["duration"], width=preview["width"], height=preview["height"],
            reply_markup=status_keyboard(request_id), request_timeout=30,
            reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None)
        try:
            await message.delete()
        except Exception:
            pass
        return sent
    except Exception as exc:
        logger.warning("Music preview unavailable: request=%s, error=%s", request_id, type(exc).__name__)
        return message
    finally:
        if claimed:
            path.unlink(missing_ok=True)
            try:
                await _payment_request("POST", f"/media-tools/{request_id}/release-preview",
                                       payload={"telegram_id": telegram_id})
            except BackendAPIError:
                pass


async def wait_result(message, telegram_id, request_id, *, reply_to=None):
    for _ in range(100):
        await asyncio.sleep(3)
        try:
            row = await _payment_request("GET", f"/media-tools/{request_id}?telegram_id={telegram_id}")
        except BackendAPIError:
            continue
        message = await maybe_show_preview(message, telegram_id, request_id, row, reply_to=reply_to)
        if row["status"] not in {"pending", "processing"}:
            await show_result(message, telegram_id, request_id, row)
            return
    await message.answer(text('processing_has_not_finished_us'), reply_markup=status_keyboard(request_id))


@router.callback_query(F.data.startswith("tool:check:") | F.data.startswith("tool:sources:"))
async def check_result(callback: CallbackQuery):
    request_id = callback.data.rsplit(":", 1)[1]
    await callback.answer()
    try:
        UUID(request_id)
        row = await _payment_request("GET", f"/media-tools/{request_id}?telegram_id={callback.from_user.id}")
        message = await maybe_show_preview(callback.message, callback.from_user.id, request_id, row)
        await show_result(message, callback.from_user.id, request_id, row,
                          auto_download=not callback.data.startswith("tool:sources:"))
    except (ValueError, BackendAPIError):
        await callback.message.answer(friendly_error("not_found"))


async def show_result(message, telegram_id, request_id, row, *, auto_download=True):
    status = row["status"]
    if status in {"pending", "processing"}:
        await update_music_card(message, html.escape(text('still_processing')), reply_markup=status_keyboard(request_id))
        return
    if status != "completed":
        prompt = text('request_cancelled') if status == "cancelled" else friendly_error(row.get("error_code") or "input_expired")
        await update_music_card(message, html.escape(prompt),
            reply_markup=recovery_keyboard(no_match=row.get("error_code") == "music_not_found"))
        return
    if row["kind"] != "music":
        await message.answer(friendly_error("tool_disabled"))
        return
    result = row.get("result") or {}
    heading = text("music_identity", title=html.escape(result.get("title", "")), artist=html.escape(result.get("artist", "")))
    candidates = result.get("candidates") or []
    if not candidates:
        await update_music_card(message, heading + "\n\n" + html.escape(text('the_song_was_identified_but')),
                                reply_markup=recovery_keyboard())
        return
    automatic = result.get("automatic_candidate")
    auto_start = auto_download and row.get("auto_mp3") and type(automatic) is int and 0 <= automatic < len(candidates)
    if auto_start:
        await update_music_card(message, heading)
        await run_track_download(message, telegram_id, request_id, automatic, "mp3", row=row, automatic=True)
        return
    rows = []
    instruction = text('tap_a_source_s_download')
    parts = [heading, html.escape(instruction)]
    for i, candidate in enumerate(candidates):
        minutes, seconds = divmod(candidate["duration"], 60)
        parts.append(html.escape(text("music_source_choice", number=i+1, title=candidate["title"][:100],
                                      duration=f"{minutes}:{seconds:02d}")))
        if candidate.get("source") == "musicfa":
            parts[-1] += '\n<a href="' + html.escape(candidate["page_url"], quote=True) + '">Music-fa · MP3 320</a>'
        rows.append([(text('download_mp_number', number=i + 1), f"tool:audio:{request_id}:{i}:mp3"),
                     (text('other_formats'), f"tool:source:{request_id}:{i}")])
    rows.append([(text("music_output_help"), "tool:about")])
    await update_music_card(message, "\n\n".join(parts), reply_markup=keyboard(rows))


@router.callback_query(F.data.in_({"tool:why", "tool:about"}))
async def music_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(text("music_no_match_help" if callback.data == "tool:why" else "music_download_help"),
                                   reply_markup=recovery_keyboard())


@router.callback_query(F.data.startswith("tool:source:"))
async def choose_source(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    request_id, index = parts[2:]
    await callback.answer()
    try:
        UUID(request_id)
        row = await _payment_request("GET", f"/media-tools/{request_id}?telegram_id={callback.from_user.id}")
        index = int(index)
        if row["kind"] != "music" or row["status"] != "completed" or not 0 <= index < len(row["result"]["candidates"]):
            raise ValueError("Invalid candidate")
    except (ValueError, KeyError, BackendAPIError):
        await callback.message.answer(friendly_error("not_found"))
        return
    buttons = [(text("format_mp3") if fmt == "mp3" else fmt.upper(), f"tool:audio:{request_id}:{index}:{fmt}") for fmt in FORMATS]
    await callback.message.answer(text('choose_the_full_track_format'),
        reply_markup=keyboard([buttons[i:i+3] for i in range(0, len(buttons), 3)]))


@router.callback_query(F.data.startswith("tool:audio:") | F.data.startswith("tool:retry:"))
async def download_track(callback: CallbackQuery):
    parts = callback.data.split(":")
    retry = len(parts) > 1 and parts[1] == "retry"
    if len(parts) != (6 if retry else 5) or parts[4] not in FORMATS:
        return
    request_id, index, output_format = parts[2:5]
    await callback.answer()
    try:
        UUID(request_id)
        index = int(index)
        failed_job = int(parts[5]) if retry else None
    except ValueError:
        await callback.message.answer(friendly_error("not_found"))
        return
    await run_track_download(callback.message, callback.from_user.id, request_id, index, output_format,
                             retry_failed_job_id=failed_job)


async def run_track_download(message, telegram_id, request_id, index, output_format, *, row=None, automatic=False,
                             retry_failed_job_id=None):
    key = (telegram_id, UUID(request_id).hex, index, output_format)
    if key in _song_downloads_in_progress:
        await message.answer(text('this_file_is_already_being'))
        return
    _song_downloads_in_progress.add(key)
    stage, job_id, status_message = "download", None, None
    try:
        if not await access(message, telegram_id):
            return
        if row is None:
            row = await _payment_request("GET", f"/media-tools/{request_id}?telegram_id={telegram_id}")
        result = row.get("result") or {}
        candidates = result.get("candidates") or []
        if row["kind"] != "music" or row["status"] != "completed" or not 0 <= index < len(candidates):
            await message.answer(friendly_error("music_not_ready"))
            return
        # Check delivery wiring before admitting a paid/quota-counted download.
        # A startup defect must not enqueue work that this process cannot follow.
        wait_for_download = _download_runtime_function("wait_for_download")
        send_downloaded_file = _download_runtime_function("send_downloaded_file")
        payload = {"telegram_id": telegram_id, "candidate": index, "output_format": output_format}
        job = await _payment_request("POST", f"/media-tools/{request_id}/download", payload={
            **payload, "automatic": automatic,
            **({"retry_failed_job_id": retry_failed_job_id} if retry_failed_job_id is not None else {})})
        job_id = job["id"]
        if job.get("delivered_at"):
            await message.answer(friendly_error("already_delivered"))
            return
        quality = text("format_mp3") if output_format == "mp3" else output_format.upper()
        info = {"title": " - ".join(filter(None, (result.get("artist"), result.get("title")))),
                "duration": candidates[index].get("duration"), "media_type": "audio",
                "music": {"title": result.get("title", ""), "artist": result.get("artist", ""),
                          "request_id": UUID(request_id).hex, "candidate": index}}
        status_message = await message.answer(text('full_track_download_queued_qua', quality=quality),
            reply_markup=build_active_download_keyboard(job_id))
        completed = await wait_for_download(job_id, status_message, quality, media_info=info)
        if completed.get("status") == "completed":
            claim = await _payment_request("POST", f"/media-tools/{request_id}/claim-song-delivery", payload=payload)
            if not claim.get("claimed"):
                await update_music_card(status_message, html.escape(friendly_error(claim.get("reason") or "delivery_started")))
                return
            stage = "delivery"
            await send_downloaded_file(
                message=status_message, status_message=status_message, job=completed, media_info=info)
    except BackendAPIError as exc:
        code = error_code(exc) or "tool_failed"
        if code in {"weekly_download_limit_reached", "daily_download_limit_reached", "concurrent_download_limit_reached",
                    "download_file_size_limit_exceeded", "download_user_blocked", "download_plan_unavailable"}:
            await message.answer(_download_runtime_function("download_error_text")(exc),
                                 reply_markup=_download_runtime_function("download_error_markup")(exc))
        else:
            await message.answer(friendly_error(code))
    except asyncio.TimeoutError:
        if stage == "delivery":
            await message.answer(friendly_error("delivery_started"))
            return
        await message.answer(text('the_download_watch_timed_out'),
            reply_markup=keyboard([[(text('check_download'), f"tool:audio:{request_id}:{index}:{output_format}")]]))
    except Exception as exc:
        # Worker errors used to escape this callback, leaving the user waiting
        # forever. Do not expose upstream URLs, tokens or response bodies.
        logger.warning("Music download failed: request=%s, job=%s, stage=%s, error=%s",
                       request_id, job_id, stage, type(exc).__name__)
        if stage == "delivery":
            body = text('telegram_delivery_could_not_be')
        else:
            detail = str(exc).lower()
            if any(word in detail for word in ("http error 403", "http 403", "sign in", "login required",
                                               "private video", "video unavailable", "requested format",
                                               "no video formats", "musicfa_source_denied", "musicfa_rate_limited")):
                body = text('the_song_was_identified_but_alt_alt')
            elif "ffmpeg" in detail or "conversion" in detail:
                body = text('the_song_was_identified_but_alt')
            else:
                body = text('the_full_track_download_failed')
        buttons = [[(text("music_back_to_sources"), f"tool:sources:{request_id}")],
                   [(text("music_request_review"), "support:open")]]
        if stage == "download" and job_id is not None:
            buttons.insert(0, [(text("music_retry_download"), f"tool:retry:{UUID(request_id).hex}:{index}:{output_format}:{job_id}")])
        if status_message is not None:
            await update_music_card(status_message, "❌ " + html.escape(body), reply_markup=keyboard(buttons))
        else:
            await message.answer("❌ " + body, reply_markup=keyboard(buttons))
    finally:
        _song_downloads_in_progress.discard(key)


@router.callback_query(F.data.startswith("tool:cancel:"))
async def cancel_tool(callback: CallbackQuery, state: FSMContext):
    request_id = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    request_id = request_id or str(data.get("tool_request_id") or "")
    await callback.answer()
    if request_id:
        try:
            UUID(request_id)
            await _payment_request("POST", f"/media-tools/{request_id}/cancel", payload={"telegram_id": callback.from_user.id})
        except BackendAPIError as exc:
            if exc.status_code != 404:
                await callback.message.answer(friendly_error(error_code(exc)))
                return
        except ValueError:
            return
    if not request_id or data.get("tool_request_id") == request_id:
        cleanup_tool_state(data)
        await state.clear()
    await callback.message.answer(text('request_cancelled_an_attempt_a'))
