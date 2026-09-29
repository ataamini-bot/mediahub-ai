"""Private coupon management: scope, capacity, preview and bound confirmation."""
import html
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.handlers.customers import SupportFormInput
from app.handlers.operations import keyboard
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError, _payment_request, get_admin_context
from app.utils.coupons import coupon_error, normalize_code, tr

router = Router(name="coupons")
router.message.filter(F.chat.type == "private", SupportFormInput())
router.callback_query.filter(F.message.chat.type == "private")
ZONE = ZoneInfo("Asia/Tehran")
FIELDS = ("code", "currency", "kind", "value", "max_discount", "starts_at", "expires_at",
          "total_limit", "per_user_limit", "plan_ids", "duration_days")
EDITABLE = FIELDS[2:] + ("active",)


class CouponStates(StatesGroup):
    field = State()
    reason = State()
    confirm = State()


def label(field):
    return {
        "code": tr("کد", "Code"), "currency": tr("ارز", "Currency"), "kind": tr("نوع تخفیف", "Discount type"),
        "value": tr("مقدار تخفیف", "Discount value"), "max_discount": tr("حداکثر مبلغ تخفیف", "Maximum discount"),
        "starts_at": tr("شروع", "Starts"), "expires_at": tr("انقضا", "Expires"),
        "total_limit": tr("سقف کل مصرف", "Total limit"), "per_user_limit": tr("سقف هر کاربر", "Per-user limit"),
        "plan_ids": tr("پلن‌های مجاز", "Allowed plans"), "duration_days": tr("مدت‌های مجاز به روز", "Allowed durations in days"),
        "active": tr("وضعیت فعال", "Enabled"),
    }[field]


def display_value(field, value):
    if value is None or value == []:
        return tr("بدون محدودیت", "Unrestricted")
    if field in {"starts_at", "expires_at"}:
        return datetime.fromisoformat(value).astimezone(ZONE).strftime("%Y-%m-%d %H:%M") + " (Asia/Tehran)"
    if field == "kind":
        return tr("درصدی", "Percentage") if value == "percent" else tr("مبلغ ثابت", "Fixed amount")
    if field == "active":
        return tr("فعال", "Enabled") if value else tr("غیرفعال", "Disabled")
    return ", ".join(map(str, value)) if isinstance(value, list) else str(value)


def terms_text(terms, plans=()):
    values = {k: display_value(k, terms.get(k)) for k in FIELDS + ("active",)}
    if terms.get("plan_ids"):
        names = {p["id"]: (p.get("name_en") if ui_language.get() == "en" and p.get("name_en") else p["name"]) for p in plans}
        values["plan_ids"] = ", ".join(names.get(i, f"#{i}") for i in terms["plan_ids"])
    return "\n".join(f"{label(k)}: <b>{html.escape(v)}</b>" for k, v in values.items())


async def allowed(event):
    message = event.message if isinstance(event, CallbackQuery) else event
    if not isinstance(message, Message) or message.chat.type != "private":
        return False
    try:
        context = await get_admin_context(event.from_user.id)
        if context.get("is_admin") and (context.get("is_superadmin") or "coupons.manage" in context.get("permissions", [])):
            return True
    except BackendAPIError:
        pass
    await event.answer(tr("دسترسی مدیریت کد تخفیف ندارید.", "Coupon management access is required."))
    return False


async def api(method, path, actor, payload=None):
    if method == "GET":
        path += ("&" if "?" in path else "?") + f"actor_telegram_id={actor}"
    return await _payment_request(method, path, **({"payload": payload} if payload is not None else {}))


async def show_list(message, actor, page=1):
    result = await api("GET", f"/admin/coupons?page={page}", actor)
    rows = [[("➕ " + tr("کد جدید", "New code"), "coupon:new")]]
    for item in result["items"]:
        title = f"{'✅' if item['active'] else '⛔'} {item['code']} · {item['currency']}"
        rows.append([(title, f"coupon:view:{item['id']}")])
    nav = []
    if page > 1:
        nav.append(("◀️", f"coupon:home:{page-1}"))
    if page * 8 < result["total"]:
        nav.append(("▶️", f"coupon:home:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([(tr("پنل مدیریت", "Admin panel"), "admin:home")])
    await message.answer(tr("🎟 کدهای تخفیف", "🎟 Discount codes"), reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("coupon:home:"))
async def home(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    await state.clear()
    try:
        await show_list(callback.message, callback.from_user.id, max(1, int(callback.data.rsplit(":", 1)[1])))
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(coupon_error(exc), show_alert=True)


async def show_detail(message, actor, coupon_id):
    item = await api("GET", f"/admin/coupons/{coupon_id}", actor)
    counts = item["counts"]
    text = terms_text(item["terms"], item.get("plans", [])) + f"\n\n{tr('مصرف‌شده', 'Redeemed')}: {counts.get('redeemed', 0)}"
    text += f" · {tr('رزروشده', 'Reserved')}: {counts.get('reserved', 0)} · {tr('آزادشده', 'Released')}: {counts.get('released', 0)}"
    text += "\n" + tr("رزروها از سقف کم می‌شوند؛ با لغو سفارش یا رد رسید آزاد می‌شوند.",
                       "Reservations count toward capacity until the order is cancelled or its receipt rejected.")
    text += f"\n{tr('جمع تخفیف مصرف‌شده', 'Redeemed discount total')}: {item.get('redeemed_discount', '0')} {item['terms']['currency']}"
    text += f"\n{tr('مبلغ خریدهای تأییدشده', 'Approved purchase total')}: {item.get('redeemed_amount', '0')} {item['terms']['currency']}"
    if item["recent"]:
        text += "\n\n" + tr("۱۰ رزرو یا مصرف آخر:", "Latest 10 reservations or uses:")
    for use in item["recent"]:
        snapshot = use["snapshot"]
        status = {"reserved": tr("رزرو", "Reserved"), "redeemed": tr("مصرف", "Redeemed"), "released": tr("آزاد", "Released")}[use["status"]]
        text += (f"\n\n{use['created_at'][:16]} UTC · {status}\nTelegram ID: {use['telegram_id']}"
                 f"\n{tr('تخفیف / مبلغ نهایی', 'Discount / final')}: {snapshot['discount_amount']} / {snapshot['final_price']} {snapshot['currency']}"
                 f"\n{tr('پرداخت', 'Payment')}: {use.get('payment_id') or '—'}")
    rows = [[(tr("✏️ ویرایش تنظیمات", "✏️ Edit settings"), f"coupon:edit:{coupon_id}")],
            [(tr("فهرست کدها", "Code list"), "coupon:home:1")]]
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("coupon:view:"))
async def view(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    await state.clear()
    try:
        await show_detail(callback.message, callback.from_user.id, callback.data.rsplit(":", 1)[1])
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(coupon_error(exc), show_alert=True)


@router.callback_query(F.data == "coupon:new")
async def new(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    await state.clear()
    await state.update_data(coupon_id=None, expected_version=0, coupon_terms={"active": True}, field="code")
    await prompt(callback.message, state, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("coupon:edit:"))
async def edit(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    coupon_id = callback.data.rsplit(":", 1)[1]
    try:
        item = await api("GET", f"/admin/coupons/{coupon_id}", callback.from_user.id)
        await state.clear()
        await state.update_data(coupon_id=coupon_id, expected_version=item["version"], coupon_terms=item["terms"],
                                coupon_plans=item.get("plans", []))
        await callback.message.answer(tr("فیلد موردنظر را انتخاب کنید:", "Choose a field:"),
            reply_markup=keyboard([[(label(f), f"coupon:field:{f}")] for f in EDITABLE] +
                [[(tr("بازگشت", "Back"), f"coupon:view:{coupon_id}")]]))
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(coupon_error(exc), show_alert=True)


@router.callback_query(F.data.startswith("coupon:field:"))
async def field(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    name = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    if name not in EDITABLE or not data.get("coupon_id"):
        await callback.answer(tr("فرم را دوباره باز کنید.", "Reopen the form."), show_alert=True)
        return
    await state.update_data(field=name)
    await prompt(callback.message, state, callback.from_user.id)
    await callback.answer()


async def prompt(message, state, actor):
    data = await state.get_data()
    name, terms = data["field"], data["coupon_terms"]
    await state.set_state(CouponStates.field)
    rows = []
    hint = ""
    if name in {"currency", "kind", "active"}:
        values = {"currency": [("تومان / IRT", "IRT"), ("USDT", "USDT")],
                  "kind": [(tr("درصدی", "Percentage"), "percent"), (tr("مبلغ ثابت", "Fixed"), "fixed")],
                  "active": [(tr("فعال", "Enabled"), "true"), (tr("غیرفعال", "Disabled"), "false")]}[name]
        rows = [[(title, f"coupon:choice:{name}:{value}") for title, value in values]]
    elif name == "plan_ids":
        plans = await api("GET", "/admin/coupons/options", actor)
        await state.update_data(coupon_plans=plans)
        await plan_picker(message, state)
        return
    elif name == "code":
        hint = tr("مثال WELCOME20؛ ۳ تا ۳۲ حرف انگلیسی، عدد، خط تیره یا زیرخط.", "Example WELCOME20; 3–32 letters, digits, hyphens or underscores.")
    elif name in {"starts_at", "expires_at"}:
        hint = tr("تاریخ میلادی و ساعت تهران: 2026-10-01 18:30\nبرای بدون محدودیت، - بفرستید.",
                  "Gregorian date and Tehran time: 2026-10-01 18:30\nSend - for no time limit.")
    elif name in {"total_limit", "per_user_limit", "duration_days", "max_discount"}:
        hint = tr("عدد مثبت؛ برای بدون محدودیت، - بفرستید.", "Positive number; send - for no limit.")
        if name == "duration_days":
            hint = tr("مدت‌ها به روز، با ویرگول: 30,90,180؛ یا - برای همه.", "Durations in days separated by commas: 30,90,180; or - for all.")
    elif name == "value":
        hint = tr("درصد بیشتر از صفر و کمتر از ۱۰۰.", "Percentage greater than 0 and less than 100.") if terms["kind"] == "percent" else terms["currency"]
    rows.append([(tr("انصراف", "Cancel"), "coupon:home:1")])
    await message.answer(f"{label(name)}\n{hint}\n" +
        (tr("مقدار فعلی: ", "Current value: ") + display_value(name, terms[name]) if name in terms else ""),
        reply_markup=keyboard(rows))


async def plan_picker(message, state, page=0):
    data = await state.get_data()
    plans, selected = data["coupon_plans"], data["coupon_terms"].get("plan_ids", [])
    page = min(max(0, page), max(0, (len(plans)-1)//8))
    rows = [[(f"{'✅' if p['id'] in selected else '▫️'} {p.get('name_en') if ui_language.get() == 'en' and p.get('name_en') else p['name']} ({p['duration_days']})"[:60],
              f"coupon:plan:{p['id']}:{page}")] for p in plans[page*8:page*8+8]]
    nav = []
    if page:
        nav.append(("◀️", f"coupon:plans:{page-1}"))
    if (page+1)*8 < len(plans):
        nav.append(("▶️", f"coupon:plans:{page+1}"))
    if nav:
        rows.append(nav)
    rows += [[(tr("همهٔ پلن‌ها", "All plans"), "coupon:plans:all"), (tr("✅ ادامه", "✅ Continue"), "coupon:plans:done")],
             [(tr("انصراف", "Cancel"), "coupon:home:1")]]
    await message.answer(tr("پلن‌ها را انتخاب کنید؛ انتخاب خالی یعنی همهٔ پلن‌ها.", "Select plans; an empty selection means all plans."), reply_markup=keyboard(rows))


@router.callback_query(CouponStates.field, F.data.startswith("coupon:plan"))
async def plans(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    data = await state.get_data()
    if data.get("field") != "plan_ids":
        await callback.answer()
        return
    parts = callback.data.split(":")
    terms = dict(data["coupon_terms"])
    if parts[1] == "plan":
        target = int(parts[2])
        if target not in {p["id"] for p in data["coupon_plans"]}:
            return
        ids = set(terms.get("plan_ids", []))
        ids.symmetric_difference_update({target})
        if len(ids) > 100:
            await callback.answer(tr("حداکثر ۱۰۰ پلن را انتخاب کنید.", "Select at most 100 plans."), show_alert=True)
            return
        terms["plan_ids"] = sorted(ids)
        await state.update_data(coupon_terms=terms)
        await plan_picker(callback.message, state, int(parts[3]))
    elif parts[2] == "done":
        await advance(callback.message, state, callback.from_user.id, terms.get("plan_ids", []))
    elif parts[2] == "all":
        terms["plan_ids"] = []
        await state.update_data(coupon_terms=terms)
        await plan_picker(callback.message, state)
    else:
        await plan_picker(callback.message, state, int(parts[2]))
    await callback.answer()


def parse_value(name, raw, terms):
    value = raw.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).replace("٬", "").replace("٫", ".")
    if name == "code":
        return normalize_code(value)
    if name in {"starts_at", "expires_at"}:
        return None if value == "-" else datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=ZONE).astimezone(timezone.utc).isoformat()
    if name == "duration_days":
        result = [] if value == "-" else sorted(set(int(v.strip()) for v in value.replace("،", ",").split(",")))
        if len(result) > 100 or any(v < 1 or v > 3650 for v in result):
            raise ValueError()
        return result
    if name in {"total_limit", "per_user_limit"}:
        if value == "-":
            return None
        result = int(value.replace(",", ""))
        if not 0 < result <= 1000000000:
            raise ValueError()
        return result
    if name == "max_discount" and value == "-":
        return None
    result = Decimal(value.replace(",", ""))
    if not result.is_finite() or result <= 0 or result > Decimal("99999999999999.9999") or result.as_tuple().exponent < -4:
        raise ValueError()
    if name == "value" and terms["kind"] == "percent":
        if result >= 100:
            raise ValueError()
    elif terms["currency"] == "IRT" and result != result.to_integral_value():
        raise ValueError()
    return str(result)


async def advance(message, state, actor, value):
    data = await state.get_data()
    name, terms = data["field"], dict(data["coupon_terms"])
    terms[name] = value
    if terms.get("starts_at") and terms.get("expires_at") and terms["expires_at"] <= terms["starts_at"]:
        await message.answer(tr("انقضا باید بعد از شروع باشد.", "Expiry must follow the start time."))
        return
    await state.update_data(coupon_terms=terms)
    if data.get("coupon_id"):
        next_field = "value" if name == "kind" else None
    else:
        index = FIELDS.index(name) + 1
        next_field = FIELDS[index] if index < len(FIELDS) else None
    if next_field:
        await state.update_data(field=next_field)
        await prompt(message, state, actor)
    else:
        await state.set_state(CouponStates.reason)
        await message.answer(tr("دلیل داخلی تغییر را بنویسید (۵ تا ۵۰۰ کاراکتر):", "Enter an internal reason (5–500 characters):"),
            reply_markup=keyboard([[(tr("انصراف", "Cancel"), "coupon:home:1")]]))


@router.callback_query(CouponStates.field, F.data.startswith("coupon:choice:"))
async def choice(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    _, _, name, value = callback.data.split(":")
    data = await state.get_data()
    valid = {"currency": {"IRT", "USDT"}, "kind": {"percent", "fixed"}, "active": {"true", "false"}}
    if name == data.get("field") and value in valid.get(name, set()):
        await advance(callback.message, state, callback.from_user.id, value == "true" if name == "active" else value)
    await callback.answer()


@router.message(CouponStates.field, F.text)
async def receive_field(message: Message, state: FSMContext):
    if not await allowed(message):
        return
    data = await state.get_data()
    if data["field"] in {"currency", "kind", "active", "plan_ids"}:
        await message.answer(tr("از دکمه‌ها انتخاب کنید.", "Use the selection buttons."))
        return
    try:
        value = parse_value(data["field"], message.text, data["coupon_terms"])
        await advance(message, state, message.from_user.id, value)
    except (ValueError, InvalidOperation):
        await message.answer(tr("مقدار معتبر نیست؛ مطابق راهنمای همین فیلد دوباره وارد کنید.", "Invalid value. Follow this field's instructions and try again."))


@router.message(CouponStates.reason, F.text)
async def reason(message: Message, state: FSMContext):
    if not await allowed(message):
        return
    value = message.text.strip()
    if not 5 <= len(value) <= 500:
        await message.answer(tr("دلیل باید ۵ تا ۵۰۰ کاراکتر باشد.", "Use a reason of 5–500 characters."))
        return
    await state.update_data(coupon_reason=value, coupon_request_id=str(uuid.uuid4()))
    await state.set_state(CouponStates.confirm)
    data = await state.get_data()
    await message.answer(terms_text(data["coupon_terms"], data.get("coupon_plans", [])) + "\n\n" + html.escape(value), parse_mode="HTML",
        reply_markup=keyboard([[(tr("✅ تأیید نهایی", "✅ Confirm"), "coupon:confirm")],
                               [(tr("انصراف", "Cancel"), "coupon:home:1")]]))


@router.callback_query(CouponStates.confirm, F.data == "coupon:confirm")
async def confirm(callback: CallbackQuery, state: FSMContext):
    if not await allowed(callback):
        return
    data = await state.get_data()
    coupon_id = data.get("coupon_id")
    try:
        item = await api("PUT" if coupon_id else "POST", "/admin/coupons" + (f"/{coupon_id}" if coupon_id else ""),
            callback.from_user.id, {"actor_telegram_id": callback.from_user.id, "request_id": data["coupon_request_id"],
                "expected_version": data["expected_version"], "reason": data["coupon_reason"], "terms": data["coupon_terms"]})
        await state.clear()
        await callback.answer(tr("ثبت شد.", "Saved."))
        await show_detail(callback.message, callback.from_user.id, item["id"])
    except BackendAPIError as exc:
        await callback.answer(coupon_error(exc), show_alert=True)
