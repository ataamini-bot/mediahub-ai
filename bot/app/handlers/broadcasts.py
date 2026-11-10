"""Private broadcast composer, real previews and explicit send confirmation."""
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.handlers.operations import keyboard
from app.i18n import home_action_for_text
from app.middleware.interface import ui_language
from app.runtime_config import action_for_runtime_text, all_runtime_configurations
from app.services.backend import BackendAPIError, _payment_request, get_admin_context
from app.services.broadcast_sender import deliver

router = Router(name="broadcasts")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


def tr(fa, en):
    return en if ui_language.get() == "en" else fa


class BroadcastInput(BaseFilter):
    async def __call__(self, message):
        if not message.text:
            return True
        text = message.text.strip()
        return not (text.startswith("/") or home_action_for_text(text) is not None
                    or action_for_runtime_text(text, await all_runtime_configurations()) is not None)


router.message.filter(BroadcastInput())


class BroadcastStates(StatesGroup):
    choosing = State()
    plan_choice = State()
    language_choice = State()
    mode_choice = State()
    manual = State()
    content = State()
    button = State()
    confirming = State()


def segment_labels():
    return {"all": tr("تمام کاربران", "All users"), "never_paid": tr("رایگان، بدون خرید قبلی", "Free, never purchased"),
        "active": tr("مشترکان فعال", "Active subscribers"), "plan": tr("یک پلن مشخص", "One specific plan"),
        "former": tr("مشتریان سابق", "Former customers"), "manual": tr("شناسه‌های انتخابی", "Selected Telegram IDs")}


def status_label(status):
    return {"draft": tr("پیش‌نویس", "Draft"), "queued": tr("در صف", "Queued"),
        "running": tr("در حال ارسال", "Sending"), "paused": tr("متوقف", "Paused"),
        "cancelled": tr("لغوشده", "Cancelled"), "completed": tr("پایان‌یافته", "Completed")}.get(status, status)


def issue_label(code):
    return {"flood_wait": tr("محدودیت سرعت تلگرام", "Telegram rate limit"),
        "forbidden": tr("مسدود یا غیرقابل دسترس", "Blocked or forbidden"),
        "chat_unavailable": tr("حساب در دسترس نیست", "Account unavailable"),
        "invalid_content": tr("منبع یا محتوای نامعتبر", "Invalid source or content"),
        "transport_uncertain": tr("نتیجه نامشخص؛ تکرار نمی‌شود", "Uncertain; not replayed"),
        "audience_changed": tr("مخاطب یا زبان تغییر کرده", "Audience or language changed"),
        "permission_revoked": tr("دسترسی مدیر برداشته شده", "Administrator permission revoked"),
        "database_restored": tr("لغو پس از بازیابی بکاپ", "Cancelled after backup recovery")}.get(code, code or "—")


def back():
    return [(tr("انصراف / فهرست پیام‌ها", "Cancel / Broadcast list"), "broadcast:home:1")]


async def allowed(event):
    message = event.message if isinstance(event, CallbackQuery) else event
    if not isinstance(message, Message) or message.chat.type != "private":
        return False
    try:
        context = await get_admin_context(event.from_user.id)
        if context.get("is_admin") and (context.get("is_superadmin") or "broadcasts.manage" in context.get("permissions", [])):
            return True
    except BackendAPIError:
        pass
    text = tr("دسترسی پیام همگانی ندارید یا سرویس در دسترس نیست.", "Broadcast access denied or service unavailable.")
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)
    return False


async def api(method, path, actor_id, **payload):
    if method == "GET":
        return await _payment_request(method, path + ("&" if "?" in path else "?") + f"actor_telegram_id={actor_id}")
    return await _payment_request(method, path, payload={"actor_telegram_id": actor_id, **payload})


async def error(event):
    text = tr("عملیات انجام نشد؛ وضعیت فعلی را دوباره باز کنید. پیش‌نمایش برای تأیید ۵ دقیقه اعتبار دارد.",
              "Operation failed. Reopen the current status. Preview confirmation expires after 5 minutes.")
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text, reply_markup=keyboard([back()]))


@router.callback_query(F.data.startswith("broadcast:home:"))
async def home(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    await state.clear()
    try:
        page = max(1, int(callback.data.rsplit(":", 1)[1]))
        result = await api("GET", f"/admin/broadcasts?page={page}", callback.from_user.id)
        rows = [[(tr("➕ پیام همگانی جدید", "➕ New broadcast"), "broadcast:new")]]
        rows += [[(f"#{job['id']} · {status_label(job['status'])} · {job['total']}", f"broadcast:view:{job['id']}")]
                 for job in result["items"]]
        navigation = []
        if page > 1:
            navigation.append(("◀️", f"broadcast:home:{page-1}"))
        if page * 8 < result["total"]:
            navigation.append(("▶️", f"broadcast:home:{page+1}"))
        if navigation:
            rows.append(navigation)
        rows += [[(tr("پنل مدیریت", "Admin panel"), "admin:open")]]
        await callback.message.edit_text(tr("📣 پیام‌های همگانی\n\nارسال فقط پس از پیش‌نمایش و تأیید نهایی انجام می‌شود.",
            "📣 Broadcasts\n\nMessages are sent only after preview and final confirmation."), reply_markup=keyboard(rows))
        await callback.answer()
    except (BackendAPIError, ValueError):
        await error(callback)


def report(job):
    counts = job["counts"]
    labels = {"sent": tr("ارسال موفق", "Sent"), "pending": tr("در انتظار", "Pending"),
        "sending": tr("در حال ارسال", "In flight"), "blocked": tr("مسدود توسط کاربر", "Bot blocked"),
        "failed": tr("ناموفق", "Failed"), "uncertain": tr("نتیجه نامشخص؛ بدون ارسال تکراری", "Uncertain; not replayed"),
        "skipped": tr("خارج از مخاطبان فعلی", "No longer eligible"), "cancelled": tr("لغوشده", "Cancelled")}
    lines = [f"📣 #{job['id']} · {status_label(job['status'])}",
             segment_labels()[job["payload"]["segment"]],
             f"{tr('مخاطبان پیش‌نمایش', 'Preview recipients')}: {job['total']}"]
    lines += [f"{name}: {counts.get(key, 0)}" for key, name in labels.items()]
    if job.get("last_error"):
        lines.append(f"⚠️ {issue_label(job['last_error'])}")
    if job["status"] in {"running", "queued", "paused"}:
        lines.append(tr("توقف یا لغو، پیام در حال ارسال را پس نمی‌گیرد.", "Pause or cancel cannot recall an in-flight message."))
    return "\n".join(lines)


async def show_job(message, actor_id, job):
    rows = []
    if job["status"] == "draft" and job["actor_telegram_id"] == actor_id:
        rows.append([(tr("👁 پیش‌نمایش و تأیید", "👁 Preview and confirm"), f"broadcast:preview:{job['id']}")])
    if job["status"] in {"queued", "running"}:
        rows.append([(tr("⏸ توقف", "⏸ Pause"), f"broadcast:action:pause:{job['id']}")])
    if job["status"] == "paused":
        rows.append([(tr("▶️ ادامهٔ ارسال‌های باقی‌مانده", "▶️ Resume pending deliveries"), f"broadcast:action:resume:{job['id']}")])
    if job["status"] in {"draft", "queued", "running", "paused"}:
        rows.append([(tr("⛔ لغو ارسال‌های باقی‌مانده", "⛔ Cancel pending deliveries"), f"broadcast:action:cancel:{job['id']}")])
    if any(job["counts"].get(key) for key in ("blocked", "failed", "uncertain", "skipped")):
        rows.append([(tr("جزئیات ارسال‌های ناموفق / نامشخص", "Delivery issues"), f"broadcast:issues:{job['id']}:1")])
    rows += [[(tr("🔄 تازه‌سازی", "🔄 Refresh"), f"broadcast:view:{job['id']}")], back()]
    try:
        await message.edit_text(report(job), reply_markup=keyboard(rows))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@router.callback_query(F.data.startswith("broadcast:issues:"))
async def issues(callback: CallbackQuery):
    if not await allowed(callback):
        return
    try:
        _, _, job_id, page = callback.data.split(":")
        job_id, page = int(job_id), max(1, int(page))
        result = await api("GET", f"/admin/broadcasts/{job_id}/issues?page={page}", callback.from_user.id)
        lines = [f"📣 #{job_id} · {tr('جزئیات', 'Delivery issues')}"]
        lines += [f"{item['telegram_id']} · {issue_label(item['error_code'])}" for item in result["items"]]
        nav = []
        if page > 1:
            nav.append(("◀️", f"broadcast:issues:{job_id}:{page-1}"))
        if page * 15 < result["total"]:
            nav.append(("▶️", f"broadcast:issues:{job_id}:{page+1}"))
        rows = [nav] if nav else []
        rows.append([(tr("بازگشت به گزارش", "Back to report"), f"broadcast:view:{job_id}")])
        await callback.message.edit_text("\n".join(lines), reply_markup=keyboard(rows))
        await callback.answer()
    except (BackendAPIError, ValueError):
        await error(callback)


@router.callback_query(F.data.startswith("broadcast:view:"))
async def view(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    try:
        await state.clear()
        job = await api("GET", f"/admin/broadcasts/{int(callback.data.rsplit(':', 1)[1])}", callback.from_user.id)
        await show_job(callback.message, callback.from_user.id, job)
        await callback.answer()
    except (BackendAPIError, ValueError):
        await error(callback)


@router.callback_query(F.data.startswith("broadcast:action:"))
async def control(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    try:
        _, _, action, job_id = callback.data.split(":")
        if action not in {"pause", "resume", "cancel"}:
            return
        job = await api("POST", f"/admin/broadcasts/{int(job_id)}/{action}", callback.from_user.id)
        await state.clear()
        await show_job(callback.message, callback.from_user.id, job)
        await callback.answer()
    except (BackendAPIError, ValueError):
        await error(callback)


@router.callback_query(F.data == "broadcast:new")
async def new(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    await state.clear()
    await state.set_state(BroadcastStates.choosing)
    await state.update_data(request_id=str(uuid.uuid4()), variants={}, buttons={})
    await callback.message.edit_text(tr("مخاطبان پیام را انتخاب کنید:", "Choose the audience:"),
        reply_markup=keyboard([[(label, f"broadcast:segment:{key}")] for key, label in segment_labels().items()] + [back()]))
    await callback.answer()


async def choose_language(message, state):
    await state.set_state(BroadcastStates.language_choice)
    await message.answer(tr("زبان مخاطبان را انتخاب کنید. برای هر دو زبان، دو پیام جدا می‌گیریم.",
                            "Choose recipient languages. Both requires a separate message for each language."),
        reply_markup=keyboard([[('فارسی', 'broadcast:language:fa'), ('English', 'broadcast:language:en')],
                               [(tr("هر دو زبان", "Both languages"), 'broadcast:language:all')], back()]))


@router.callback_query(BroadcastStates.choosing, F.data.startswith("broadcast:segment:"))
async def segment(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    value = callback.data.rsplit(":", 1)[1]
    if value not in segment_labels():
        return
    await state.update_data(segment=value, plan_id=None, telegram_ids=[])
    try:
        if value == "plan":
            plans = await api("GET", "/admin/broadcasts/plans", callback.from_user.id)
            await state.set_state(BroadcastStates.plan_choice)
            rows = [[(str(p.get("name_en") or p["name"]) if ui_language.get() == "en" else p["name"], f"broadcast:plan:{p['id']}")] for p in plans]
            await callback.message.answer(tr("پلن موردنظر را انتخاب کنید:", "Choose a plan:"), reply_markup=keyboard(rows + [back()]))
        elif value == "manual":
            await state.set_state(BroadcastStates.manual)
            await callback.message.answer(tr("شناسه‌های عددی کاربران ثبت‌شده را با فاصله یا خط جدید بفرستید (حداکثر ۱۰۰۰).",
                "Send registered users' numeric Telegram IDs, separated by spaces or newlines (up to 1000)."), reply_markup=keyboard([back()]))
        else:
            await choose_language(callback.message, state)
        await callback.answer()
    except BackendAPIError:
        await error(callback)


@router.callback_query(BroadcastStates.plan_choice, F.data.startswith("broadcast:plan:"))
async def plan(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    try:
        await state.update_data(segment="plan", plan_id=int(callback.data.rsplit(":", 1)[1]))
        await choose_language(callback.message, state)
        await callback.answer()
    except ValueError:
        await error(callback)


@router.message(BroadcastStates.manual, F.text)
async def manual(message: Message, state: FSMContext):
    if not await allowed(message):
        return
    try:
        ids = sorted(set(int(x) for x in message.text.replace(",", " ").replace("،", " ").split()))
        if not ids or len(ids) > 1000 or any(i <= 0 or i > 2**63-1 for i in ids):
            raise ValueError()
        await state.update_data(telegram_ids=ids)
        await choose_language(message, state)
    except ValueError:
        await message.answer(tr("فقط شناسهٔ عددی مثبت وارد کنید.", "Enter positive numeric Telegram IDs only."))


@router.callback_query(BroadcastStates.language_choice, F.data.startswith("broadcast:language:"))
async def language(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    value = callback.data.rsplit(":", 1)[1]
    if value not in {"all", "fa", "en"}:
        return
    await state.update_data(language=value, variants={}, buttons={}, current_language="fa" if value == "all" else value)
    await state.set_state(BroadcastStates.mode_choice)
    await callback.message.answer(tr("نوع ارسال را انتخاب کنید:", "Choose the message mode:"), reply_markup=keyboard([
        [(tr("متن / عکس / ویدئو / فایل", "Text / photo / video / document"), "broadcast:mode:content")],
        [(tr("فوروارد پست کانال", "Forward a channel post"), "broadcast:mode:forward")], back()]))
    await callback.answer()


async def ask_content(message, state):
    data = await state.get_data()
    await state.set_state(BroadcastStates.content)
    lang = "فارسی" if data["current_language"] == "fa" else "English"
    prompt = tr("پست کانال را اینجا فوروارد کنید", "Forward the channel post here") if data["mode"] == "forward" else tr("یک متن یا یک عکس، ویدئو یا فایل با توضیح دلخواه بفرستید", "Send text or one photo, video or document with an optional caption")
    await message.answer(f"{lang}\n{prompt}", reply_markup=keyboard([back()]))


@router.callback_query(BroadcastStates.mode_choice, F.data.startswith("broadcast:mode:"))
async def mode(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    value = callback.data.rsplit(":", 1)[1]
    if value not in {"content", "forward"}:
        return
    await state.update_data(mode=value)
    await ask_content(callback.message, state)
    await callback.answer()


def content_from_message(message, mode):
    if message.media_group_id or message.has_protected_content:
        raise ValueError("Unsupported album or protected content")
    if mode == "forward":
        if message.forward_origin is None:
            raise ValueError("Forward a post")
        return {"kind": "forward", "source_chat_id": message.chat.id, "source_message_id": message.message_id}
    for kind in ("photo", "video", "document"):
        media = getattr(message, kind)
        if media:
            media = media[-1] if kind == "photo" else media
            return {"kind": kind, "file_id": media.file_id, "text": message.caption or "",
                    "entities": [e.model_dump(mode="json", exclude_none=True) for e in message.caption_entities or []]}
    if message.text:
        return {"kind": "text", "text": message.text,
                "entities": [e.model_dump(mode="json", exclude_none=True) for e in message.entities or []]}
    raise ValueError("Unsupported content")


@router.message(BroadcastStates.content)
async def content(message: Message, state: FSMContext):
    if not await allowed(message):
        return
    data = await state.get_data()
    try:
        value = content_from_message(message, data["mode"])
        variants = {**data["variants"], data["current_language"]: value}
        await state.update_data(variants=variants)
        await state.set_state(BroadcastStates.button)
        if value["kind"] == "forward":
            await finish_variant(message, state)
        else:
            await message.answer(tr("دکمهٔ لینک اختیاری: عنوان و آدرس را به شکل زیر بفرستید یا «بدون دکمه» را بزنید.\nعنوان | https://example.com",
                "Optional URL button: send label and URL as below, or choose No button.\nLabel | https://example.com"),
                reply_markup=keyboard([[(tr("بدون دکمه", "No button"), "broadcast:button:skip")], back()]))
    except (ValueError, KeyError):
        await message.answer(tr("این پیام پشتیبانی نمی‌شود؛ یک پیام تکی و قابل ارسال بفرستید. در حالت فوروارد، پست را فوروارد کنید.",
            "Unsupported message. Send one unprotected message; forward a post when using Forward mode."))
    except BackendAPIError:
        await error(message)


@router.message(BroadcastStates.button, F.text)
async def button(message: Message, state: FSMContext):
    if not await allowed(message):
        return
    try:
        title, url = [part.strip() for part in message.text.split("|", 1)]
        parsed = urlsplit(url)
        if not 1 <= len(title) <= 60 or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError()
        data = await state.get_data()
        await state.update_data(buttons={**data["buttons"], data["current_language"]: {"text": title, "url": url}})
        await finish_variant(message, state)
    except (ValueError, KeyError):
        await message.answer(tr("فرمت صحیح: عنوان | https://example.com", "Use: Label | https://example.com"))
    except BackendAPIError:
        await error(message)


@router.callback_query(BroadcastStates.button, F.data == "broadcast:button:skip")
async def skip_button(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    try:
        await finish_variant(callback.message, state, actor_id=callback.from_user.id)
        await callback.answer()
    except BackendAPIError:
        await error(callback)


async def finish_variant(message, state, actor_id=None):
    data = await state.get_data()
    if data["language"] == "all" and data["current_language"] == "fa":
        await state.update_data(current_language="en")
        await ask_content(message, state)
        return
    actor_id = actor_id or message.from_user.id
    payload = {key: data[key] for key in ("request_id", "segment", "language", "plan_id", "telegram_ids", "variants", "buttons")}
    job = await api("POST", "/admin/broadcasts", actor_id, **payload)
    await preview_job(message, state, actor_id, job)


async def preview_job(message, state, actor_id, job):
    if job["status"] != "draft" or job["actor_telegram_id"] != actor_id or datetime.fromisoformat(job["expires_at"]) < datetime.now(timezone.utc):
        await message.answer(tr("این پیش‌نویس منقضی یا تأیید شده است؛ پیام جدید بسازید.", "This draft expired or was confirmed. Create a new broadcast."), reply_markup=keyboard([back()]))
        return
    # Send the exact payload to the administrator first. No queue is started by preview.
    try:
        for lang, variant in job["payload"]["variants"].items():
            await message.answer(f"👁 {'فارسی' if lang == 'fa' else 'English'}")
            await deliver(message.bot, actor_id, variant, job["payload"]["buttons"].get(lang))
    except Exception:
        await message.answer(tr("پیش‌نمایش ارسال نشد؛ منبع پیام را بررسی کنید. هیچ ارسال همگانی شروع نشده است.",
            "Preview failed. Check the source message. No broadcast has started."), reply_markup=keyboard([back()]))
        return
    await state.set_state(BroadcastStates.confirming)
    await state.update_data(broadcast_id=job["id"], confirmation_token=job["confirmation_token"])
    counts = job["languages"]
    text = (f"📣 #{job['id']}\n{segment_labels()[job['payload']['segment']]}\n"
            f"فارسی: {counts.get('fa', 0)} · English: {counts.get('en', 0)}\n\n" +
            tr("ارسال همین پیام‌ها به مخاطبان بالا تأیید می‌شود؟", "Send these exact messages to the recipients counted above?"))
    rows = [[(tr("✅ تأیید نهایی و شروع ارسال", "✅ Confirm and start sending"), "broadcast:confirm")]] if job["total"] else []
    await message.answer(text, reply_markup=keyboard(rows + [back()]))


@router.callback_query(F.data.startswith("broadcast:preview:"))
async def preview(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    try:
        await state.clear()
        job = await api("GET", f"/admin/broadcasts/{int(callback.data.rsplit(':', 1)[1])}", callback.from_user.id)
        await preview_job(callback.message, state, callback.from_user.id, job)
        await callback.answer()
    except (BackendAPIError, ValueError):
        await error(callback)


@router.callback_query(BroadcastStates.confirming, F.data == "broadcast:confirm")
async def confirm(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    data = await state.get_data()
    try:
        job = await api("POST", f"/admin/broadcasts/{data['broadcast_id']}/confirm", callback.from_user.id,
                        confirmation_token=data["confirmation_token"])
        await state.clear()
        await show_job(callback.message, callback.from_user.id, job)
        await callback.answer(tr("ارسال در صف قرار گرفت.", "Broadcast queued."))
    except (BackendAPIError, KeyError):
        await error(callback)
