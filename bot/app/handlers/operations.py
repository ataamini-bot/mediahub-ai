from app.localization import tr as _tr, localized_collection as _localized_collection
import html
import json
from datetime import date
from urllib.parse import urlencode

import aiohttp
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.backend import BackendAPIError, BACKEND_URL, _internal_headers, _payment_request

router = Router(name="operations")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class OperationStates(StatesGroup):
    value = State()
    confirming = State()
    dates = State()
    audit_actor = State()


LABELS = {
    "notifications.enabled": "اعلان‌ها", "notifications.chat_id": "شناسه گروه مدیران",
    "notifications.topic.monitoring": "Topic مانیتورینگ", "notifications.topic.payments": "Topic پرداخت‌ها",
    "notifications.topic.backups": "Topic بکاپ", "notifications.topic.system": "Topic سیستم",
    "notifications.topic.support": "Topic پشتیبانی", "monitor.interval_seconds": "فاصله بررسی (ثانیه)",
    "monitor.bot_heartbeat_seconds": "مهلت پاسخ Bot (ثانیه)", "monitor.disk_percent": "آستانه Disk (%)",
    "monitor.ram_percent": "آستانه RAM (%)", "monitor.cpu_percent": "آستانه CPU (%)",
    "monitor.celery_queue_size": "حد هشدار تعداد صف", "monitor.download_error_percent": "حد درصد خطای دانلود",
    "monitor.download_error_window_minutes": "بازه خطای دانلود (دقیقه)",
}
PERIODS = {"1yr": "۱ سال", "6mo": "۶ ماه", "3mo": "۳ ماه", "1mo": "۱ ماه", "7d": "۷ روز", "today": "روزانه", "all": "همه", "custom": "بازه دلخواه"}


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=callback) for text, callback in row
    ] for row in rows])


async def api(method, path, actor, **payload):
    if method in {"GET", "POST"} and not payload:
        path += ("&" if "?" in path else "?") + urlencode({"actor_telegram_id": actor})
    return await _payment_request(method, path, payload={"actor_telegram_id": actor, **payload} if payload else None)


async def show_operations(message, actor_id):
    result = await api("GET", "/admin/operations", actor_id)
    lines = [_tr("🖥 <b>مانیتورینگ و اعلان‌ها</b>"), ""]
    snapshot = result.get("snapshot")
    if snapshot:
        lines.append(_tr("آخرین بررسی: ") + html.escape(snapshot["observed_at"]))
        for name, healthy in snapshot.get("services", {}).items():
            value = (snapshot.get("metrics", {}).get(name) or {}).get("value")
            lines.append(f"{'✅' if healthy else '❌'} {name}: {html.escape(str(value if value is not None else '—'))}")
    else:
        lines.append(_tr("⚠️ گزارش تازه مانیتور موجود نیست؛ وضعیت Monitor و Redis را بررسی کنید."))
    rows = []
    if result.get("can_manage"):
        for setting in result.get("settings", []):
            key = setting["key"]
            if key.startswith("notifications.") and not result.get("is_superadmin"):
                continue
            rows.append([(f"{_tr(LABELS.get(key, key))}: {setting.get('value')}", f"ops:edit:{key}")])
        rows.extend([[(f"{_tr('📨 تست ')}{topic}", f"ops:test:{topic}")] for topic in result["routes"]["topics"]])
    rows += [[(_tr("🔄 به‌روزرسانی"), "ops:open")], [(_tr("🔙 پنل"), "admin:open")]]
    await message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard(rows))


@router.callback_query(F.data == "ops:open")
async def open_operations(callback: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await show_operations(callback.message, callback.from_user.id)
        await callback.answer()
    except BackendAPIError:
        await callback.answer(_tr("دریافت مانیتورینگ ممکن نشد یا دسترسی ندارید."), show_alert=True)


@router.callback_query(F.data.startswith("ops:edit:"))
async def edit_operation(callback: CallbackQuery, state: FSMContext):
    key = callback.data.removeprefix("ops:edit:")
    try:
        result = await api("GET", "/admin/operations", callback.from_user.id)
        row = next(x for x in result["settings"] if x["key"] == key)
        if not result["can_manage"] or (key.startswith("notifications.") and not result["is_superadmin"]):
            raise ValueError()
        await state.set_state(OperationStates.value)
        await state.update_data(operation=row)
        hint = _tr("true یا false") if key.endswith("enabled") else _tr("عدد صحیح")
        await callback.message.answer(f"{_tr(LABELS.get(key, key))}{_tr(' — مقدار جدید را بفرستید (')}{hint}):")
        await callback.answer()
    except (BackendAPIError, ValueError, StopIteration):
        await callback.answer(_tr("ویرایش این تنظیم ممکن نیست."), show_alert=True)


@router.message(OperationStates.value, F.text)
async def receive_value(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        row = data["operation"]
        raw = message.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if row["key"].endswith("enabled"):
            if raw.lower() not in {"true", "false"}:
                raise ValueError()
            value = raw.lower() == "true"
        else:
            value = int(raw)
        await state.update_data(value=value)
        await state.set_state(OperationStates.confirming)
        await message.answer(f"{_tr('تغییر ')}{_tr(LABELS.get(row['key'], row['key']))}{_tr(' به ')}{value}{_tr(' تأیید شود؟')}",
            reply_markup=keyboard([[(_tr("✅ تأیید نهایی"), "ops:confirm")], [(_tr("انصراف"), "ops:open")]]))
    except (ValueError, KeyError):
        await message.answer(_tr("مقدار معتبر وارد کنید."))


@router.callback_query(OperationStates.confirming, F.data == "ops:confirm")
async def confirm_operation(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        row = data["operation"]
        await api("PUT", f"/admin/operations/{row['key']}", callback.from_user.id,
            value=data["value"], expected_version=row["version"])
        await state.clear()
        await show_operations(callback.message, callback.from_user.id)
        await callback.answer(_tr("تنظیم ثبت شد؛ تا ۳۰ ثانیه اعمال می‌شود."))
    except BackendAPIError as exc:
        await callback.answer(str(exc.detail)[:180], show_alert=True)


@router.callback_query(F.data.startswith("ops:test:"))
async def topic_test(callback: CallbackQuery):
    topic = callback.data.rsplit(":", 1)[-1]
    try:
        await api("POST", f"/admin/operations/test/{topic}", callback.from_user.id)
        await callback.answer(_tr("پیام آزمایشی ارسال شد."), show_alert=True)
    except BackendAPIError as exc:
        await callback.answer(str(exc.detail)[:180], show_alert=True)


async def report_menu(message, kind):
    await message.edit_text(_tr("📅 بازه گزارش را انتخاب کنید:"), reply_markup=keyboard(
        [[(label, f"reports:period:{kind}:{period}")] for period, label in PERIODS.items()]
        + [[(_tr("🔙 پنل"), "admin:open")]]))


@router.callback_query(F.data.in_({"reports:finance", "reports:audit"}))
async def open_report(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await report_menu(callback.message, callback.data.rsplit(":", 1)[-1])
    await callback.answer()


async def render_report(message, state, actor_id, *, page=1):
    data = await state.get_data()
    kind = data["report_kind"]
    query = {"period": data["period"], "page": page}
    query.update({key: data[key] for key in ("start", "end", "filter_actor") if data.get(key)})
    report = await api("GET", f"/admin/reports/{kind}?{urlencode(query)}", actor_id)
    rows = []
    if kind == "finance":
        lines = [_tr("📊 <b>گزارش مالی</b>"), f"{_tr('منطقه زمانی: ')}{report['timezone']}",
            _tr("مبنای پرداخت بررسی‌شده: زمان بررسی؛ در انتظار: زمان ثبت"), ""]
        for row in report["totals"]:
            lines.append(f"{row['currency']} / {row['status']}: {row['count']} — {row['amount']}")
        lines.append(_tr("\nتفکیک پلن / مدت / روش / ارز / وضعیت:"))
        for row in report["items"]:
            lines.append(f"• {html.escape(row['plan'])} / {row['duration_days']}{_tr(' روز / ')}{row['method']} / {row['currency']} / {row['status']}: {row['count']} — {row['amount']}")
        rows.append([(_tr("📥 خروجی CSV همین بازه"), "reports:csv")])
    else:
        lines = [_tr("📜 <b>Audit Log و فعالیت مدیران</b>"), ""]
        for row in report["activity"]:
            lines.append(f"👮 {row['actor']}: {row['actions']}{_tr(' عملیات / ')}{row['failed']}{_tr(' ناموفق')}")
        for row in report["items"]:
            lines.append(f"\n#{row['id']} / {html.escape(row['created_at'])}\n{row['actor']} / {html.escape(row['action'])} / {html.escape(str(row['target_id']))} / {'✅' if row['success'] else '❌'}")
            rows.append([(f"{_tr('جزئیات Audit #')}{row['id']}", f"reports:detail:{row['id']}")])
        await state.update_data(audit_items=report["items"])
        rows.append([(_tr("👮 فیلتر مدیر"), "reports:actor")])
    nav = []
    if page > 1:
        nav.append((_tr("◀️ قبلی"), f"reports:page:{page-1}"))
    if page * report["page_size"] < report["total"]:
        nav.append((_tr("بعدی ▶️"), f"reports:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows += [[(_tr("📅 بازه دیگر"), f"reports:{kind}")], [(_tr("🔙 پنل"), "admin:open")]]
    await message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("reports:period:"))
async def choose_report_period(callback: CallbackQuery, state: FSMContext):
    _, _, kind, period = callback.data.split(":")
    if kind not in {"finance", "audit"} or period not in PERIODS:
        return
    await state.clear()
    await state.update_data(report_kind=kind, period=period)
    if period == "custom":
        await state.set_state(OperationStates.dates)
        await callback.message.answer(_tr("تاریخ شروع و پایان میلادی را بفرستید؛ مثال: 2026-09-01 2026-09-11"))
    else:
        try:
            await render_report(callback.message, state, callback.from_user.id)
        except BackendAPIError:
            await callback.answer(_tr("گزارش در دسترس نیست یا مجوز ندارید."), show_alert=True)
            return
    await callback.answer()


@router.message(OperationStates.dates, F.text)
async def receive_dates(message: Message, state: FSMContext):
    try:
        start, end = [date.fromisoformat(x) for x in message.text.strip().split()]
        if start > end:
            raise ValueError()
        await state.set_state(None)
        await state.update_data(start=start.isoformat(), end=end.isoformat())
        target = await message.answer(_tr("در حال دریافت گزارش…"))
        await render_report(target, state, message.from_user.id)
    except (ValueError, BackendAPIError):
        await message.answer(_tr("بازه معتبر YYYY-MM-DD YYYY-MM-DD وارد کنید."))


@router.callback_query(F.data.startswith("reports:page:"))
async def report_page(callback: CallbackQuery, state: FSMContext):
    try:
        await render_report(callback.message, state, callback.from_user.id, page=max(1,int(callback.data.rsplit(":",1)[-1])))
        await callback.answer()
    except (ValueError, KeyError, BackendAPIError):
        await callback.answer(_tr("گزارش را دوباره باز کنید."), show_alert=True)


@router.callback_query(F.data == "reports:csv")
async def export_csv(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    query = {key: data[key] for key in ("period", "start", "end") if data.get(key)}
    query["actor_telegram_id"] = callback.from_user.id
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(f"{BACKEND_URL}/admin/reports/finance.csv", params=query, headers=_internal_headers()) as response:
                if response.status != 200:
                    raise BackendAPIError(status_code=response.status, detail="Export failed; choose a shorter date range or check permissions")
                content = await response.read()
        await callback.message.answer_document(BufferedInputFile(content, filename="mediahub-finance.csv"))
        await callback.answer()
    except (BackendAPIError, aiohttp.ClientError):
        await callback.answer(_tr("خروجی ممکن نشد؛ دسترسی یا بازه را بررسی کنید (حداکثر ۱۰٬۰۰۰ ردیف)."), show_alert=True)


@router.callback_query(F.data == "reports:actor")
async def ask_audit_actor(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OperationStates.audit_actor)
    await callback.message.answer(_tr("Telegram ID مدیر را بفرستید؛ برای همه عدد 0:"))
    await callback.answer()


@router.message(OperationStates.audit_actor, F.text)
async def audit_actor(message: Message, state: FSMContext):
    try:
        actor_id = int(message.text.strip())
        if actor_id < 0:
            raise ValueError()
        await state.update_data(filter_actor=actor_id or None)
        await state.set_state(None)
        target = await message.answer(_tr("در حال دریافت گزارش…"))
        await render_report(target, state, message.from_user.id)
    except (ValueError, BackendAPIError, KeyError):
        await message.answer(_tr("شناسه یا گزارش معتبر نیست؛ دوباره باز کنید."))


@router.callback_query(F.data.startswith("reports:detail:"))
async def audit_detail(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    row = next((r for r in data.get("audit_items",[]) if str(r["id"]) == callback.data.rsplit(":",1)[-1]), None)
    if row:
        # Recheck permission before exposing cached audit details.
        await api("GET", "/admin/reports/audit?period=all&page_size=1", callback.from_user.id)
        await callback.message.answer(json.dumps(row, ensure_ascii=False, indent=2), parse_mode=None)
    await callback.answer()


# Resolve static labels using the language of the current update.
LABELS = _localized_collection(LABELS)
PERIODS = _localized_collection(PERIODS)
