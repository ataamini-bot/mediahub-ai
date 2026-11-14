"""Private, consent-first music recognition and portrait workflows."""
import asyncio
import html
import os
from pathlib import Path
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.conversion import _attachment, _download_telegram_file, _download_runtime_function, cleanup_staged_state
from app.middleware.interface import ui_language
from app.keyboards.download import build_active_download_keyboard
from app.runtime_config import runtime_configuration
from app.services.backend import BackendAPIError, _payment_request, get_download_entitlement, register_telegram_user

router = Router(name="media_tools")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
ROOT = Path(os.environ.get("DOWNLOAD_DIR", "/app/downloads")) / "tools"
LIMITS = {"music": 25 * 1024**2, "portrait": 10 * 1024**2}
FORMATS = ("mp3", "m4a", "wav", "aac", "flac", "ogg", "opus")
CLOTHES = {"natural": ("لباس فعلی خودم", "Keep my clothing"), "suit": ("کت‌وشلوار", "Suit"),
           "diplomat": ("کت‌وشلوار و یقه دیپلمات", "Suit & mandarin collar"), "shirt": ("پیراهن", "Shirt")}
BACKGROUNDS = {"white": ("سفید", "White"), "blue": ("آبی روشن", "Light blue"), "grey": ("خاکستری", "Grey")}
_uploads_in_progress = set()


class ToolStates(StatesGroup):
    upload = State()
    options = State()
    confirmation = State()
    working = State()


def text(fa, en):
    return en if ui_language.get() == "en" else fa


def label(mapping, value):
    return mapping[value][1 if ui_language.get() == "en" else 0]


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, callback_data=data) for title, data in row] for row in rows])


def cancel_row(request_id=""):
    return [(text("انصراف", "Cancel"), "tool:cancel:" + request_id)]


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
    messages = {
        "tool_disabled": ("این قابلیت هنوز توسط مدیر فعال نشده است.", "This tool has not been enabled by the administrator."),
        "tool_daily_limit": ("سهمیه امروز این قابلیت تمام شده است؛ فردا دوباره امتحان کنید.", "Today's tool limit has been reached. Try again tomorrow."),
        "tool_busy": ("یک درخواست شما در حال پردازش است؛ ابتدا نتیجه آن را دریافت کنید.", "You already have a request in progress. Wait for its result."),
        "music_not_found": ("آهنگ شناسایی نشد. یک نمونه واضح‌تر با حداقل ۱۰ ثانیه موسیقی بفرستید.", "No song was identified. Try a clearer sample with at least 10 seconds of music."),
        "invalid_audio": ("فایل صدای قابل خواندن ندارد. ویس یا کلیپی بفرستید که آهنگ در ابتدای آن پخش شود.", "No readable audio. Send a voice message or clip with music at the beginning."),
        "invalid_photo": ("یک عکس واضح JPG، PNG یا WebP با حداقل اندازه ۲۰۰ پیکسل بفرستید.", "Send a clear JPG, PNG or WebP photo at least 200 pixels on each side."),
        "provider_uncertain": ("ارتباط با سرویس قطع شد و نتیجه مشخص نیست. برای جلوگیری از مصرف دوباره، درخواست خودکار تکرار نمی‌شود.", "The provider connection was interrupted. The result is unknown, so the paid request will not be repeated automatically."),
        "provider_auth": ("اتصال سرویس نیاز به بررسی مدیر دارد؛ لطفاً بعداً تلاش کنید.", "The provider connection needs the administrator's attention. Try later."),
        "provider_busy": ("سرویس فعلاً ظرفیت پاسخ‌گویی ندارد؛ کمی بعد تلاش کنید.", "The provider is busy or its quota is exhausted. Try later."),
        "input_expired": ("فایل موقت منقضی شده است؛ فایل را دوباره ارسال کنید.", "The temporary upload expired. Send the file again."),
        "not_found": ("این درخواست در دسترس نیست.", "This request is unavailable."),
        "download_start_uncertain": ("درخواست دانلود قبلاً ثبت شده یا در حال ثبت است. از تکرار آن جلوگیری شد؛ وضعیت دانلود را بررسی کنید.", "The download is already starting or its confirmation was lost. Check your download status."),
        "maintenance_mode": ("ربات موقتاً در حال تعمیر است.", "The bot is temporarily under maintenance."),
        "downloads_disabled": ("شروع درخواست‌های جدید موقتاً بسته است.", "New requests are temporarily disabled."),
    }
    fa, en = messages.get(code, ("درخواست انجام نشد؛ کمی بعد دوباره امتحان کنید.", "The request could not be completed. Try again later."))
    return text(fa, en)


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
    if kind == "music":
        prompt = text(
            "🎵 <b>تشخیص آهنگ و دریافت نسخه کامل</b>\n\n"
            "ویس، فایل صوتی یا کلیپی بفرستید که آهنگ در ابتدای آن واضح شنیده شود (حداقل ۱۰ ثانیه، حداکثر ۲۵ مگابایت).\n"
            "ابتدا نام آهنگ و خواننده را پیدا می‌کنیم؛ سپس منبع نسخه کامل را برای انتخاب شما نشان می‌دهیم. خروجی دانلود از آن منبع است، نه صدای همین کلیپ.\n"
            "این قابلیت برای موسیقی ضبط‌شده است؛ تشخیص زمزمه یا نامی که با صدای خودتان می‌گویید تضمین نمی‌شود.",
            "🎵 <b>Identify a song and get the full track</b>\n\n"
            "Send a voice message, audio file or clip with clear music at the beginning (at least 10 seconds, up to 25 MB).\n"
            "We identify the title and artist, then show full-track sources for you to choose. The download comes from that source, not your sample.\n"
            "This is for recorded music; humming or speaking a song title may not be recognized.")
    else:
        prompt = text(
            "📸 <b>ساخت عکس پرسنلی</b>\n\n"
            "یک عکس واضح از روبه‌رو با چهره و شانه‌های کامل بفرستید؛ برای کیفیت بهتر، آن را به‌صورت فایل ارسال کنید (JPG، PNG یا WebP، حداکثر ۱۰ مگابایت).\n"
            "بعد لباس و پس‌زمینه را انتخاب می‌کنید. نور و کنتراست به‌صورت ملایم اصلاح می‌شود و از تغییر چهره پرهیز می‌کنیم.\n"
            "عکس خودتان یا فردی را بفرستید که برای این ویرایش اجازه داده است.",
            "📸 <b>Portrait photo</b>\n\n"
            "Send a clear front-facing photo showing the whole face and shoulders. For best quality, send it as a file (JPG, PNG or WebP, up to 10 MB).\n"
            "Next choose clothing and background. We request mild lighting and contrast correction while preserving your facial features.\n"
            "Use your own photo or one you have permission to edit.")
    await message.answer(prompt, parse_mode="HTML", reply_markup=keyboard([cancel_row()]))


@router.callback_query(F.data.in_({"tool:open:music", "tool:open:portrait"}))
async def open_tool(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await register_telegram_user(callback)
    await send_tool_prompt(callback.message, state, callback.data.rsplit(":", 1)[1], callback.from_user.id)


@router.message(ToolStates.upload, F.photo | F.document | F.audio | F.voice | F.video | F.video_note | F.animation)
async def upload(message: Message, state: FSMContext):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id in _uploads_in_progress:
        await message.answer(text("دریافت فایل قبلی هنوز تمام نشده است.", "The previous upload is still being received."))
        return
    _uploads_in_progress.add(user_id)
    try:
        await stage_upload(message, state)
    finally:
        _uploads_in_progress.discard(user_id)


async def stage_upload(message, state):
    data = await state.get_data()
    kind = data.get("tool_kind")
    if kind not in LIMITS or not message.from_user:
        return
    if kind == "portrait":
        item = message.photo[-1] if message.photo else message.document
    else:
        attached = _attachment(message)
        item = attached[0] if attached else None
    if item is None:
        await message.answer(text("نوع فایل مناسب این قابلیت نیست.", "This file type is not suitable for this tool."))
        return
    size = getattr(item, "file_size", None)
    if not isinstance(size, int) or not 0 < size <= LIMITS[kind]:
        await message.answer(text(f"حداکثر حجم مجاز {LIMITS[kind] // 1024**2} مگابایت است.",
                                  f"The file limit is {LIMITS[kind] // 1024**2} MB."))
        return
    request_id = uuid4().hex
    path = tool_folder(message.from_user.id, request_id)
    cleanup_tool_state(data)
    await state.update_data(tool_request_id=request_id, tool_owner=message.from_user.id, tool_queued=False)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        await _download_telegram_file(message.bot, item.file_id, path / "input", expected_size=size, timeout=90)
        if not 0 < (path / "input").stat().st_size <= LIMITS[kind]:
            raise ValueError("File limit exceeded")
    except Exception:
        cleanup_tool_state(await state.get_data())
        await message.answer(text("دریافت فایل کامل نشد؛ دوباره ارسال کنید.", "The upload was not completed. Send it again."))
        return
    if kind == "music":
        await confirmation(message, state, request_id)
    else:
        await state.set_state(ToolStates.options)
        rows = [[(label(CLOTHES, key), f"tool:clothes:{request_id}:{key}")] for key in CLOTHES]
        await message.answer(text("لباس عکس را انتخاب کنید:", "Choose the clothing:"), reply_markup=keyboard(rows + [cancel_row(request_id)]))


async def valid_state(callback, state, request_id):
    data = await state.get_data()
    if data.get("tool_request_id") != request_id or data.get("tool_owner") != callback.from_user.id or data.get("tool_queued"):
        await callback.answer(text("این منو منقضی شده است.", "This menu expired."), show_alert=True)
        return None
    return data


@router.callback_query(F.data.startswith("tool:clothes:"))
async def choose_clothes(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) != 4 or parts[3] not in CLOTHES:
        return
    request_id, clothing = parts[2:]
    data = await valid_state(callback, state, request_id)
    if not data or data["tool_kind"] != "portrait":
        return
    await state.update_data(tool_clothing=clothing)
    await callback.answer()
    rows = [[(label(BACKGROUNDS, key), f"tool:bg:{request_id}:{key}")] for key in BACKGROUNDS]
    await callback.message.edit_text(text("رنگ پس‌زمینه را انتخاب کنید:", "Choose the background color:"),
        reply_markup=keyboard(rows + [cancel_row(request_id)]))


@router.callback_query(F.data.startswith("tool:bg:"))
async def choose_background(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) != 4 or parts[3] not in BACKGROUNDS:
        return
    request_id, background = parts[2:]
    data = await valid_state(callback, state, request_id)
    if not data or data.get("tool_clothing") not in CLOTHES:
        return
    await state.update_data(tool_background=background)
    await callback.answer()
    await confirmation(callback.message, state, request_id)


async def confirmation(message, state, request_id):
    data = await state.get_data()
    await state.set_state(ToolStates.confirmation)
    if data["tool_kind"] == "music":
        prompt = text("با تأیید شما، نمونه‌ای از ۱۲ ثانیه اول فایل برای شناسایی به سرویس AudD ارسال می‌شود. "
            "این تلاش از سهمیه روزانه تشخیص آهنگ مصرف می‌کند. دانلود نسخه کامل، پس از انتخاب شما، از سهمیه عادی دانلود است.",
            "Confirm to send a sample of the first 12 seconds to AudD. This uses one daily recognition attempt. "
            "Downloading a full track after your selection uses your normal download quota.")
    else:
        clothes, bg = label(CLOTHES, data["tool_clothing"]), label(BACKGROUNDS, data["tool_background"])
        prompt = text(f"لباس: {clothes}\nپس‌زمینه: {bg}\nاصلاح نور و کنتراست: ملایم\n\n"
            "با تأیید شما، عکس برای ویرایش به سرویس OpenAI ارسال می‌شود و یک تلاش از سهمیه روزانه مصرف می‌کند. "
            "فایل‌های موقت پردازش پس از ارسال خروجی از سرور ربات پاک می‌شوند. نگهداری داده در سرویس خارجی تابع مقررات همان سرویس است. "
            "خروجی را از نظر شباهت چهره بررسی کنید؛ پذیرش آن برای مدارک رسمی تضمین نمی‌شود.",
            f"Clothing: {clothes}\nBackground: {bg}\nLighting and contrast: mild\n\n"
            "Confirm to send your photo to OpenAI for editing and use one daily attempt. "
            "Temporary processing files are removed from this bot's server after delivery. The external provider has its own data retention policy. "
            "Check facial likeness; acceptance for official documents is not guaranteed.")
    await message.answer(prompt, reply_markup=keyboard([
        [(text("تأیید و شروع", "Confirm and start"), f"tool:start:{request_id}")], cancel_row(request_id)]))


def status_keyboard(request_id):
    return keyboard([[(text("بررسی نتیجه", "Check result"), f"tool:check:{request_id}")], cancel_row(request_id)])


@router.callback_query(F.data.startswith("tool:start:"))
async def start_tool(callback: CallbackQuery, state: FSMContext):
    request_id = callback.data.rsplit(":", 1)[1]
    try:
        UUID(request_id)
    except ValueError:
        return
    data = await state.get_data()
    if data.get("tool_request_id") != request_id or data.get("tool_owner") != callback.from_user.id:
        await callback.answer(text("این منو منقضی شده است.", "This menu expired."), show_alert=True)
        return
    if data.get("tool_kind") == "portrait" and (data.get("tool_clothing") not in CLOTHES or data.get("tool_background") not in BACKGROUNDS):
        await callback.answer(text("ابتدا لباس و پس‌زمینه را انتخاب کنید.", "Choose clothing and background first."), show_alert=True)
        return
    await callback.answer()
    if not await access(callback.message, callback.from_user.id):
        return
    # Mark the handoff before HTTP so a lost response cannot cause cleanup to
    # delete a file that a Worker already accepted.
    await state.update_data(tool_queued=True)
    await state.set_state(ToolStates.working)
    payload = {"request_id": request_id, "telegram_id": callback.from_user.id, "kind": data["tool_kind"],
               "clothing": data.get("tool_clothing", "natural"), "background": data.get("tool_background", "white")}
    try:
        await _payment_request("POST", "/media-tools", payload=payload)
    except BackendAPIError as exc:
        code = error_code(exc)
        if code != "backend_unavailable":
            cleanup_tool_state({**data, "tool_queued": False})
            await state.clear()
        await callback.message.answer(friendly_error(code), reply_markup=status_keyboard(request_id) if code == "backend_unavailable" else None)
        return
    await callback.message.edit_text(text("⏳ درخواست ثبت شد؛ در حال پردازش…", "⏳ Request accepted. Processing…"),
        reply_markup=status_keyboard(request_id))
    await wait_result(callback.message, callback.from_user.id, request_id)


async def wait_result(message, telegram_id, request_id):
    for _ in range(100):
        await asyncio.sleep(3)
        try:
            row = await _payment_request("GET", f"/media-tools/{request_id}?telegram_id={telegram_id}")
        except BackendAPIError:
            continue
        if row["status"] not in {"pending", "processing"}:
            await show_result(message, telegram_id, request_id, row)
            return
    await message.answer(text("پردازش هنوز پایان نیافته؛ با دکمه «بررسی نتیجه» پیگیری کنید.",
                              "Processing has not finished. Use Check result to continue."), reply_markup=status_keyboard(request_id))


@router.callback_query(F.data.startswith("tool:check:"))
async def check_result(callback: CallbackQuery):
    request_id = callback.data.rsplit(":", 1)[1]
    await callback.answer()
    try:
        UUID(request_id)
        row = await _payment_request("GET", f"/media-tools/{request_id}?telegram_id={callback.from_user.id}")
        await show_result(callback.message, callback.from_user.id, request_id, row)
    except (ValueError, BackendAPIError):
        await callback.message.answer(friendly_error("not_found"))


async def show_result(message, telegram_id, request_id, row):
    status = row["status"]
    if status in {"pending", "processing"}:
        await message.answer(text("⏳ هنوز در حال پردازش است.", "⏳ Still processing."), reply_markup=status_keyboard(request_id))
        return
    if status != "completed":
        prompt = text("درخواست لغو شد.", "Request cancelled.") if status == "cancelled" else friendly_error(row.get("error_code") or "input_expired")
        await message.answer(prompt)
        return
    if row["kind"] == "portrait":
        path = tool_folder(telegram_id, request_id) / "result.jpg"
        if row.get("files_removed") or not path.is_file() or path.is_symlink():
            await message.answer(text("این عکس قبلاً ارسال شده یا فایل موقت آن منقضی شده است.", "This photo was already sent or its temporary file expired."))
            return
        claim = await _payment_request("POST", f"/media-tools/{request_id}/claim-delivery", payload={"telegram_id": telegram_id})
        if not claim.get("claimed"):
            await message.answer(text("ارسال این عکس قبلاً آغاز شده است. اگر فایل نرسیده، با پشتیبانی تماس بگیرید.",
                                      "Delivery already started. Contact support if the photo did not arrive."))
            return
        # Send as a document to preserve full resolution. An uncertain send is
        # never replayed by the poller, nor does it start another paid edit.
        try:
            await message.answer_document(FSInputFile(path, filename="portrait.jpg"),
                caption=text("📸 عکس شما آماده است. پیش از استفاده، جزئیات چهره را بررسی کنید.",
                             "📸 Your portrait is ready. Check the facial details before using it."))
        except Exception:
            await message.answer(text("ارسال عکس تأیید نشد. با پشتیبانی تماس بگیرید؛ ساخت عکس تکرار نشده است.",
                                      "Photo delivery could not be confirmed. Contact support; no new photo generation was started."))
            return
        path.unlink(missing_ok=True)
        try:
            await _payment_request("POST", f"/media-tools/{request_id}/release", payload={"telegram_id": telegram_id})
        except BackendAPIError:
            pass
        return
    result = row.get("result") or {}
    heading = f"🎵 {html.escape(result.get('title', ''))}\n👤 {html.escape(result.get('artist', ''))}"
    candidates = result.get("candidates") or []
    if not candidates:
        await message.answer(heading + "\n\n" + text("آهنگ شناسایی شد، اما منبع قابل دانلود برای نسخه کامل پیدا نشد. صدای کلیپ شما به‌جای آهنگ ارسال نمی‌شود.",
            "The song was identified, but no matching full-track download source was found. Your sample will not be sent as a substitute."), parse_mode="HTML")
        return
    rows = []
    parts = [heading, text("منبع نسخه کامل را بررسی و انتخاب کنید؛ تطبیق نام و مدت به معنی تضمین نسخه رسمی یا فایل مستر نیست.",
                           "Review and choose a full-track source. Title and duration matching do not guarantee an official master recording.")]
    for i, candidate in enumerate(candidates):
        minutes, seconds = divmod(candidate["duration"], 60)
        parts.append(f"{i+1}. {html.escape(candidate['title'])}\n{html.escape(candidate['channel'])} · {minutes}:{seconds:02d}\n{candidate['url']}")
        rows.append([(text(f"انتخاب منبع {i+1}", f"Choose source {i+1}"), f"tool:source:{request_id}:{i}")])
    await message.answer("\n\n".join(parts), parse_mode="HTML", reply_markup=keyboard(rows))


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
    buttons = [(fmt.upper(), f"tool:audio:{request_id}:{index}:{fmt}") for fmt in FORMATS]
    await callback.message.answer(text("فرمت دریافت آهنگ کامل را انتخاب کنید. تبدیل به WAV یا FLAC کیفیت منبع را بیشتر نمی‌کند.",
        "Choose the full-track audio format. WAV or FLAC conversion cannot improve the source quality."),
        reply_markup=keyboard([buttons[i:i+3] for i in range(0, len(buttons), 3)]))


@router.callback_query(F.data.startswith("tool:audio:"))
async def download_track(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 5 or parts[4] not in FORMATS:
        return
    request_id, index, output_format = parts[2:]
    await callback.answer()
    try:
        UUID(request_id)
        if not await access(callback.message, callback.from_user.id):
            return
        job = await _payment_request("POST", f"/media-tools/{request_id}/download", payload={
            "telegram_id": callback.from_user.id, "candidate": int(index), "output_format": output_format})
        status_message = await callback.message.answer(text("⏳ دانلود آهنگ کامل در صف است…", "⏳ Full-track download queued…"),
            reply_markup=build_active_download_keyboard(job["id"]))
        completed = await _download_runtime_function("wait_for_download")(job["id"], status_message, output_format.upper())
        if completed.get("status") == "completed":
            await _download_runtime_function("send_downloaded_file")(message=status_message, status_message=status_message, job=completed)
    except BackendAPIError as exc:
        await callback.message.answer(friendly_error(error_code(exc)))
    except (ValueError, KeyError):
        await callback.message.answer(friendly_error("not_found"))


@router.callback_query(F.data.startswith("tool:cancel:"))
async def cancel_tool(callback: CallbackQuery, state: FSMContext):
    request_id = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
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
    await callback.message.answer(text("درخواست لغو شد. اگر پردازش سرویس شروع شده باشد، سهمیه مصرف‌شده برنمی‌گردد.",
        "Request cancelled. An attempt already started by the provider still counts toward the daily limit."))
