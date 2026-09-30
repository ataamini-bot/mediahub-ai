"""Private, permission-checked customer support forms."""
import html
import uuid
from urllib.parse import urlencode

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.handlers.operations import api, keyboard
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError
from app.i18n import home_action_for_text
from app.runtime_config import action_for_runtime_text, all_runtime_configurations


class SupportFormInput(BaseFilter):
    async def __call__(self, message):
        text = str(message.text or "").strip()
        if not text or text.startswith("/") or home_action_for_text(text) is not None:
            return False
        return action_for_runtime_text(text, await all_runtime_configurations()) is None

router = Router(name="customers")
router.message.filter(F.chat.type == "private")
router.message.filter(SupportFormInput())
router.callback_query.filter(F.message.chat.type == "private")


def tr(fa, en):
    return en if ui_language.get() == "en" else fa


def label(action):
    return {"block": tr("مسدودکردن", "Block"), "unblock": tr("رفع مسدودی", "Unblock"),
        "grant": tr("اعطای اشتراک", "Grant subscription"), "extend": tr("افزایش مدت", "Extend"),
        "cancel": tr("لغو اشتراک انتخاب‌شده", "Cancel selected subscription"),
        "change_plan": tr("تغییر پلن اشتراک", "Change subscription plan"),
        "reset_quota": tr("بازنشانی سهمیه", "Reset quotas"), "note": tr("یادداشت داخلی", "Internal note")}[action]


class CustomerStates(StatesGroup):
    search = State()
    select = State()
    days = State()
    reason = State()
    confirm = State()


async def show_profile(message, actor_id, telegram_id):
    result = await api("GET", f"/admin/customers/{telegram_id}", actor_id)
    lines = [tr("👤 مشتری", "👤 Customer"), html.escape(result["name"]),
        f"Telegram ID: <code>{telegram_id}</code>",
        f"{tr('وضعیت', 'Status')}: {result['status']}",
        f"{tr('پلن', 'Plan')}: {html.escape(result['plan_name'])}",
        f"{tr('خروجی تحویل‌شده در بازه', 'Delivered in quota period')}: {result['used']} / {result['limit'] if result['limit'] is not None else '∞'} ({result['period']})",
        f"{tr('پرداخت منتظر بررسی', 'Pending payments')}: {result['pending_payments']}"]
    for sub in result["subscriptions"]:
        lines.append(f"#{sub['id']} · {html.escape(sub['plan_name'])}\n{sub['started_at'][:10]} → {sub['expires_at'][:10]} (UTC)")
    rows = [[(tr("🧾 فعالیت دانلود", "🧾 Download activity"), f"customer:downloads:{telegram_id}:1")]]
    actions = []
    from app.handlers.credit import money
    for currency in ("IRT", "USDT"):
        lines.append(tr("اعتبار: ", "Credit: ") + money(result.get("credits", {}).get(currency, "0"), currency))
    if result.get("can_manage_credit"):
        rows.append([(tr("💰 مدیریت اعتبار", "💰 Manage credit"), f"credit:admin:view:{telegram_id}:IRT:1")])
    if result["can_manage_users"]:
        actions += ["unblock" if result["status"] == "blocked" else "block", "reset_quota", "note"]
    if result["can_manage_subscriptions"]:
        actions += ["grant"]
        if result["subscriptions"]:
            actions += ["extend", "change_plan", "cancel"]
    for action in actions:
        rows.append([(label(action), f"customer:action:{action}:{telegram_id}")])
    if result["history"]:
        lines.append("\n" + tr("آخرین اقدامات پشتیبانی:", "Recent support actions:"))
        for row in result["history"][:4]:
            lines.append(f"{label(row['action'])} · {row['actor']}\n{html.escape(row['reason'][:160])}")
    rows += [[(tr("جست‌وجوی مشتری", "Search customers"), "customer:open")], [(tr("پنل مدیریت", "Admin panel"), "admin:open")]]
    await message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard(rows))


@router.callback_query(F.data.regexp(r"^customer:downloads:([1-9][0-9]*):([1-9][0-9]*)$"))
async def download_activity(callback: CallbackQuery):
    from urllib.parse import urlsplit
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    telegram_id, page = map(int, callback.data.split(":")[-2:])
    try:
        data = await api("GET", f"/admin/customers/{telegram_id}/downloads?page={page}", callback.from_user.id)
    except BackendAPIError:
        await callback.answer(tr("دسترسی یا ارتباط با سرور بررسی شود.", "Check access or backend connection."), show_alert=True)
        return
    lines = [tr("🧾 فعالیت دانلود کاربر", "🧾 User download activity"), f"Telegram ID: {telegram_id}",
             tr("تعداد: ", "Total: ") + str(data["total"])]
    rows = []
    for item in data["items"]:
        lines.extend(["", f"#{item['id']} · {html.escape(item['status'])} · {html.escape(item.get('media_type') or 'media')}",
                      item["created_at"][:19].replace("T", " ") + " (UTC)"])
        source = str(item.get("source_url") or "")
        if urlsplit(source).scheme in {"http", "https"}:
            lines.append(html.escape(source[:280]) + ("…" if len(source) > 280 else ""))
            rows.append([InlineKeyboardButton(text=tr("🔗 لینک درخواست ", "🔗 Request link ") + str(item["id"]), url=source)])
        else:
            lines.append(tr("فایل ارسالی کاربر", "User-uploaded file"))
        lines.append(tr("فایل حذف شده", "File removed") if item.get("files_removed_at") else tr("فایل موقت / در انتظار پاک‌سازی", "Temporary file / cleanup pending"))
    if not data["items"]:
        lines.append(tr("درخواستی ثبت نشده است.", "No download requests recorded."))
    nav = []
    if data["page"] > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"customer:downloads:{telegram_id}:{data['page']-1}"))
    if data["page"] * 5 < data["total"]:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"customer:downloads:{telegram_id}:{data['page']+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=tr("بازگشت به مشتری", "Back to customer"), callback_data=f"customer:view:{telegram_id}")])
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "customer:open")
async def open_customers(callback: CallbackQuery, state: FSMContext):
    # Search endpoint checks users.view even before the user enters a query.
    try:
        await api("GET", "/admin/customers?q=0", callback.from_user.id)
        await state.clear()
        await state.set_state(CustomerStates.search)
        await callback.message.answer(tr("شناسه تلگرام، نام کاربری یا نام مشتری را بفرستید:", "Send a Telegram ID, username or customer name:"),
            reply_markup=keyboard([[(tr("انصراف", "Cancel"), "admin:open")]]))
        await callback.answer()
    except BackendAPIError:
        await callback.answer(tr("دسترسی یا ارتباط با سرور بررسی شود.", "Check access or backend connection."), show_alert=True)


async def search_results(message, actor_id, query, page):
    result = await api("GET", "/admin/customers?" + urlencode({"q": query, "page": page}), actor_id)
    rows = [[(f"{r['name'][:25]} · {r['telegram_id']}", f"customer:view:{r['telegram_id']}")] for r in result["items"]]
    nav = []
    if page > 1:
        nav.append(("◀️", f"customer:page:{page - 1}"))
    if page * 10 < result["total"]:
        nav.append(("▶️", f"customer:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([(tr("پنل مدیریت", "Admin panel"), "admin:open")])
    await message.answer(f"{tr('نتایج', 'Results')}: {result['total']}", reply_markup=keyboard(rows))


@router.message(CustomerStates.search, F.text)
async def find_customer(message: Message, state: FSMContext):
    query = message.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not 1 <= len(query) <= 100:
        return await message.answer(tr("عبارت جست‌وجو معتبر نیست.", "Invalid search query."))
    try:
        await state.update_data(customer_query=query)
        await search_results(message, message.from_user.id, query, 1)
    except BackendAPIError:
        await message.answer(tr("جست‌وجو انجام نشد؛ دوباره تلاش کنید.", "Search failed; please retry."))


@router.callback_query(F.data.startswith("customer:page:"))
async def search_page(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        await search_results(callback.message, callback.from_user.id, data["customer_query"], int(callback.data.rsplit(":", 1)[-1]))
        await callback.answer()
    except (KeyError, ValueError, BackendAPIError):
        await callback.answer(tr("جست‌وجو را دوباره باز کنید.", "Open search again."), show_alert=True)


@router.callback_query(F.data.startswith("customer:view:"))
async def view_customer(callback: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        await show_profile(callback.message, callback.from_user.id, int(callback.data.rsplit(":", 1)[-1]))
        await callback.answer()
    except (ValueError, BackendAPIError):
        await callback.answer(tr("اطلاعات مشتری دریافت نشد.", "Could not load the customer."), show_alert=True)


async def ask_reason(message, state):
    await state.set_state(CustomerStates.reason)
    await message.answer(tr("دلیل یا یادداشت داخلی را بنویسید (۵ تا ۵۰۰ حرف):", "Enter an internal reason or note (5–500 characters):"),
        reply_markup=keyboard([[(tr("انصراف", "Cancel"), "customer:open")]]))


async def ask_plan(message, actor_id, state):
    plans = await api("GET", "/admin/customers/plans", actor_id)
    await state.update_data(available_plans=plans)
    rows = [[((p.get("name_en") or f"Plan {p['id']}") if ui_language.get() == "en" else p["name"], f"customer:plan:{p['id']}")] for p in plans]
    rows.append([(tr("انصراف", "Cancel"), "customer:open")])
    await message.answer(tr("پلن را انتخاب کنید:", "Choose a plan:"), reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("customer:action:"))
async def action(callback: CallbackQuery, state: FSMContext):
    try:
        _, _, selected_action, target = callback.data.split(":")
        if selected_action not in {"block", "unblock", "reset_quota", "note", "grant", "extend", "cancel", "change_plan"}:
            raise ValueError()
        profile = await api("GET", f"/admin/customers/{int(target)}", callback.from_user.id)
        permission = "can_manage_subscriptions" if selected_action in {"grant", "extend", "cancel", "change_plan"} else "can_manage_users"
        if not profile[permission]:
            raise ValueError()
        await state.clear()
        await state.set_state(CustomerStates.select)
        await state.update_data(target=int(target), action=selected_action, profile=profile,
            request_id=str(uuid.uuid4()), expected_revision=profile["revision"])
        if selected_action == "grant":
            await ask_plan(callback.message, callback.from_user.id, state)
        elif selected_action in {"extend", "cancel", "change_plan"}:
            rows = [[(f"#{s['id']} · {s['plan_name']}", f"customer:sub:{s['id']}")] for s in profile["subscriptions"]]
            rows.append([(tr("انصراف", "Cancel"), "customer:open")])
            await callback.message.answer(tr("اشتراک موردنظر را انتخاب کنید:", "Choose the subscription:"), reply_markup=keyboard(rows))
        else:
            await ask_reason(callback.message, state)
        await callback.answer()
    except (ValueError, BackendAPIError):
        await callback.answer(tr("این عملیات مجاز یا در دسترس نیست.", "This action is unavailable or not allowed."), show_alert=True)


@router.callback_query(CustomerStates.select, F.data.startswith("customer:sub:"))
async def choose_subscription(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sub = next((s for s in data["profile"]["subscriptions"] if str(s["id"]) == callback.data.rsplit(":", 1)[-1]), None)
    if not sub:
        return await callback.answer(tr("اشتراک معتبر نیست.", "Invalid subscription."), show_alert=True)
    await state.update_data(subscription_id=sub["id"])
    if data["action"] == "change_plan":
        await ask_plan(callback.message, callback.from_user.id, state)
    elif data["action"] == "extend":
        await state.set_state(CustomerStates.days)
        await callback.message.answer(tr("چند روز اضافه شود؟ (۱ تا ۳۶۵۰)", "How many days to add? (1–3650)"))
    else:
        await ask_reason(callback.message, state)
    await callback.answer()


@router.callback_query(CustomerStates.select, F.data.startswith("customer:plan:"))
async def choose_plan(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan = next((p for p in data.get("available_plans", []) if str(p["id"]) == callback.data.rsplit(":", 1)[-1]), None)
    if not plan:
        return await callback.answer(tr("پلن معتبر نیست.", "Invalid plan."), show_alert=True)
    await state.update_data(plan_id=plan["id"], plan_name=plan["name"])
    if data["action"] == "grant":
        await state.set_state(CustomerStates.days)
        await callback.message.answer(tr("مدت اعطا چند روز باشد؟ (۱ تا ۳۶۵۰)", "Grant for how many days? (1–3650)"))
    else:
        await ask_reason(callback.message, state)
    await callback.answer()


@router.message(CustomerStates.days, F.text)
async def receive_days(message: Message, state: FSMContext):
    try:
        days = int(message.text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        if not 1 <= days <= 3650:
            raise ValueError()
    except ValueError:
        return await message.answer(tr("عدد بین ۱ تا ۳۶۵۰ وارد کنید.", "Enter a number from 1 to 3650."))
    await state.update_data(days=days)
    await ask_reason(message, state)


@router.message(CustomerStates.reason, F.text)
async def receive_reason(message: Message, state: FSMContext):
    reason = message.text.strip()
    if not 5 <= len(reason) <= 500:
        return await message.answer(tr("دلیل باید ۵ تا ۵۰۰ حرف باشد.", "The reason must be 5–500 characters."))
    await state.update_data(reason=reason)
    data = await state.get_data()
    effects = {
        "grant": tr("اشتراک جدید پس از دوره‌های فعلی و رزروشده شروع می‌شود؛ پرداختی ثبت نمی‌شود.", "The grant starts after existing entitlements; no payment is recorded."),
        "extend": tr("اشتراک‌های بعدی به همان تعداد روز جابه‌جا می‌شوند.", "Later subscriptions move forward by the same number of days."),
        "cancel": tr("فقط اشتراک انتخاب‌شده لغو می‌شود؛ این عملیات بازپرداخت وجه نیست.", "Only the selected subscription is cancelled. This does not refund a payment."),
        "change_plan": tr("مدت حفظ می‌شود؛ محدودیت‌های اشتراک با پلن انتخاب‌شده جایگزین می‌شوند.", "Dates are preserved; subscription limits are replaced by the selected plan."),
        "reset_quota": tr("سهمیه دانلود و تبدیل بازنشانی می‌شود؛ کارهای در صف همچنان ظرفیت رزرو می‌کنند.", "Download and conversion usage is reset; queued jobs still reserve quota."),
        "block": tr("درخواست‌های جدید مسدود می‌شوند؛ دانلودهای در حال اجرا ادامه دارند.", "New requests are blocked; running downloads continue."),
    }
    details = "\n".join(f"{key}: {html.escape(str(data[key]))}" for key in ("plan_name", "subscription_id", "days") if key in data)
    await state.set_state(CustomerStates.confirm)
    await message.answer(f"{label(data['action'])}\nTelegram ID: <code>{data['target']}</code>\n{details}\n\n{effects.get(data['action'], '')}\n\n{html.escape(reason)}",
        parse_mode="HTML", reply_markup=keyboard([[(tr("✅ تأیید نهایی", "✅ Confirm"), "customer:confirm")], [(tr("انصراف", "Cancel"), "customer:open")]]))


@router.callback_query(CustomerStates.confirm, F.data == "customer:confirm")
async def confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    payload = {key: data[key] for key in ("request_id", "expected_revision", "action", "reason", "plan_id", "subscription_id", "days") if key in data}
    try:
        result = await api("POST", f"/admin/customers/{data['target']}/actions", callback.from_user.id, **payload)
        await state.clear()
        notice = ""
        if data["action"] != "note" and not result["replayed"]:
            lang = result["customer"]["language"]
            try:
                await callback.bot.send_message(data["target"], "Your account or subscription was updated by support. Open your subscription page for details." if lang == "en" else "حساب یا اشتراک شما توسط پشتیبانی به‌روزرسانی شد. جزئیات را در بخش اشتراک بررسی کنید.")
            except Exception:
                notice = tr(" اطلاع‌رسانی به مشتری ناموفق بود.", " Customer notification failed.")
        await callback.message.answer(tr("✅ تغییر ثبت شد.", "✅ Change saved.") + notice)
        await callback.answer()
    except BackendAPIError:
        await callback.answer(tr("تغییر ثبت نشد یا وضعیت عوض شده است؛ پروفایل را دوباره باز کنید.", "Change failed or the profile changed; reopen it before retrying."), show_alert=True)
        return
    try:
        await show_profile(callback.message, callback.from_user.id, data["target"])
    except BackendAPIError:
        await callback.message.answer(tr("تغییر ثبت شده؛ برای دریافت وضعیت تازه، پروفایل را دوباره باز کنید.", "Change is saved; reopen the profile to refresh its status."))
