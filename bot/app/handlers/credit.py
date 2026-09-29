"""Private balance history, reviewed admin adjustments and full-credit checkout."""
import html
import uuid
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.handlers.customers import SupportFormInput
from app.handlers.operations import keyboard
from app.middleware.interface import ui_language
from app.services.backend import BackendAPIError, _payment_request, get_admin_context, get_telegram_user

router = Router(name="credit")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


def tr(fa, en):
    return en if ui_language.get() == "en" else fa


def money(value, currency):
    amount = Decimal(str(value))
    return f"{amount:,.0f} {tr('تومان', 'IRT')}" if currency == "IRT" else f"{amount:,.4f} USDT"


def error_text(exc):
    detail = exc.detail if isinstance(exc, BackendAPIError) else {}
    code = detail.get("code") if isinstance(detail, dict) else detail
    return {
        "credit_insufficient": tr("اعتبار کافی نیست؛ کل مبلغ سفارش باید از اعتبار همان ارز پرداخت شود.", "Insufficient credit. The full order total must be paid in the same currency."),
        "credit_stale_balance": tr("موجودی تغییر کرده؛ حساب را دوباره باز و تغییر را بررسی کنید.", "The balance changed. Reopen the account and review the adjustment."),
        "credit_user_not_found": tr("این شناسه در ربات ثبت نشده است.", "This Telegram ID is not registered."),
        "credit_invalid_reversal": tr("فقط تغییر موجودی مدیر قابل برگشت است.", "Only an administrator adjustment can be reversed."),
        "credit_already_reversed": tr("این تغییر قبلاً برگشت خورده است.", "This adjustment was already reversed."),
        "credit_order_closed": tr("این سفارش بسته شده است؛ وضعیت خرید را بررسی کنید.", "This order is closed. Check your purchase status."),
        "payment_order_language": tr("زبان حساب با ارز سفارش یکی نیست؛ سفارش را لغو و دوباره انتخاب کنید.", "Your language no longer matches the order currency. Cancel it and choose again."),
        "open_payment_order_exists": tr("یک سفارش باز دارید؛ از خرید اشتراک آن را ادامه دهید یا لغو کنید.", "An order is already open. Resume or cancel it from Buy subscription."),
        "pending_payment_exists": tr("یک پرداخت منتظر بررسی دارید؛ ابتدا نتیجهٔ آن مشخص شود.", "A payment is awaiting review. Wait for its result first."),
        "payments_disabled": tr("خرید اشتراک موقتاً غیرفعال است.", "Purchases are temporarily disabled."),
        "maintenance_mode": tr("ربات موقتاً در حال نگهداری است.", "The bot is temporarily under maintenance."),
    }.get(code, tr("عملیات انجام نشد؛ دسترسی یا وضعیت فعلی را بررسی و دوباره باز کنید.", "Operation failed. Check access and reopen the current status."))


class CreditStates(StatesGroup):
    target = State()
    view = State()
    amount = State()
    reason = State()
    confirm = State()
    purchase = State()


async def admin_allowed(event):
    message = event.message if isinstance(event, CallbackQuery) else event
    if not isinstance(message, Message) or message.chat.type != "private":
        return False
    try:
        context = await get_admin_context(event.from_user.id)
        if context.get("is_admin") and (context.get("is_superadmin") or "balances.manage" in context.get("permissions", [])):
            return True
    except BackendAPIError:
        pass
    if isinstance(event, CallbackQuery):
        await event.answer(tr("دسترسی مدیریت اعتبار ندارید.", "Credit management access is required."), show_alert=True)
    else:
        await event.answer(tr("دسترسی مدیریت اعتبار ندارید.", "Credit management access is required."))
    return False


def entries_text(account, *, admin=False):
    labels = {"adjustment": tr("تغییر مدیر", "Admin adjustment"), "reversal": tr("برگشت تغییر", "Reversal"),
              "purchase": tr("خرید اشتراک", "Subscription purchase")}
    lines = []
    for item in account["items"]:
        sign = "+" if Decimal(item["delta"]) > 0 else ""
        lines.append(f"\n{item['created_at'][:16].replace('T', ' ')} UTC · {labels[item['kind']]}\n"
                     f"{sign}{money(item['delta'], account['currency'])} → {money(item['balance_after'], account['currency'])}")
        if admin:
            lines.append(f"{tr('مدیر / کاربر', 'Actor')}: {item['actor']}\n{html.escape(item['reason'])}")
            if item.get("reversed"):
                lines.append(tr("↩️ برگشت ثبت شده", "↩️ Reversed"))
            notice_labels = {"pending": tr("در صف", "Queued"), "sending": tr("در حال ارسال", "Sending"),
                "sent": tr("ارسال‌شده", "Sent"), "uncertain": tr("نامشخص", "Uncertain"),
                "failed": tr("ناموفق", "Failed"), "disabled": tr("غیرفعال", "Disabled")}
            notices = item.get("notices", {})
            if notices:
                lines.append(tr("اعلان: ", "Notification: ") + " · ".join(
                    (tr("کاربر", "User") if k == "user" else tr("گزارش", "Report")) + ": " + notice_labels.get(v, v)
                    for k, v in notices.items()))
    return "\n".join(lines) or tr("هنوز تراکنشی ثبت نشده است.", "No transactions yet.")


def page_rows(account, prefix):
    page, nav = account["page"], []
    if page > 1:
        nav.append(("◀️", f"{prefix}:{page-1}"))
    if page * 8 < account["total"]:
        nav.append(("▶️", f"{prefix}:{page+1}"))
    return [nav] if nav else []


async def show_mine(message, actor, page=1):
    account = await _payment_request("GET", f"/credit/me?telegram_id={actor}&page={page}")
    text = f"{tr('💰 اعتبار داخلی', '💰 Internal credit')}\n\n{tr('موجودی', 'Balance')}: <b>{money(account['balance'], account['currency'])}</b>\n"
    text += tr("قابل استفاده برای خرید کامل اشتراک؛ برداشت و انتقال ندارد.\n", "For full subscription purchases; no withdrawals or transfers.\n")
    text += entries_text(account)
    rows = page_rows(account, "credit:mine")
    rows += [[(tr("خرید اشتراک", "Buy subscription"), "payment:open")],
             [(tr("اشتراک من", "My subscription"), "payment:status")]]
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard(rows))


@router.message(Command("balance"))
async def balance_command(message: Message, state: FSMContext):
    await state.clear()
    try:
        await show_mine(message, message.from_user.id)
    except BackendAPIError as exc:
        await message.answer(error_text(exc))


@router.callback_query(F.data.startswith("credit:mine:"))
async def mine(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await show_mine(callback.message, callback.from_user.id, max(1, int(callback.data.rsplit(":", 1)[1])))
        await callback.answer()
    except (BackendAPIError, ValueError) as exc:
        await callback.answer(error_text(exc), show_alert=True)


@router.callback_query(F.data == "credit:admin")
async def admin_home(callback: CallbackQuery, state: FSMContext):
    if not await admin_allowed(callback):
        return
    await state.clear()
    await state.set_state(CreditStates.target)
    await callback.message.answer(tr("شناسهٔ عددی تلگرام مشتری را بفرستید:", "Send the customer's numeric Telegram ID:"),
        reply_markup=keyboard([[(tr("پنل مدیریت", "Admin panel"), "admin:open")]]))
    await callback.answer()


async def show_admin(message, actor, target, currency, state, page=1):
    account = await _payment_request("GET", f"/admin/credit/{target}?actor_telegram_id={actor}&currency={currency}&page={page}")
    await state.clear()
    await state.set_state(CreditStates.view)
    await state.update_data(credit_target=target, credit_currency=currency, credit_account=account)
    text = f"{tr('💰 اعتبار مشتری', '💰 Customer credit')}\n{html.escape(account['name'])}\nTelegram ID: <code>{target}</code>\n"
    text += f"{tr('موجودی', 'Balance')}: <b>{money(account['balance'], currency)}</b>\n{entries_text(account, admin=True)}"
    prefix = f"credit:admin:view:{target}"
    rows = [[(tr("تومان", "IRT"), f"{prefix}:IRT:1"), ("USDT", f"{prefix}:USDT:1")],
            [(tr("➕ افزایش", "➕ Add"), f"credit:adjust:credit:{target}:{currency}"),
             (tr("➖ کاهش", "➖ Deduct"), f"credit:adjust:debit:{target}:{currency}")]]
    for item in account["items"]:
        if item["kind"] == "adjustment" and not item["reversed"]:
            rows.append([(tr("↩️ برگشت تغییر ", "↩️ Reverse ") + item["id"][:8], f"credit:reverse:{item['id']}")])
    rows += page_rows(account, f"{prefix}:{currency}")
    rows += [[(tr("🔄 تازه‌سازی", "🔄 Refresh"), f"{prefix}:{currency}:{page}")],
             [(tr("مشتری دیگر", "Another customer"), "credit:admin"), (tr("پنل مدیریت", "Admin panel"), "admin:open")]]
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard(rows))


@router.message(CreditStates.target, SupportFormInput(), F.text)
async def receive_target(message: Message, state: FSMContext):
    if not await admin_allowed(message):
        return
    try:
        target = int(message.text.strip())
        if not 0 < target < 2**63:
            raise ValueError()
        await show_admin(message, message.from_user.id, target, "IRT", state)
    except ValueError:
        await message.answer(tr("شناسهٔ عددی مثبت وارد کنید.", "Enter a positive numeric Telegram ID."))
    except BackendAPIError as exc:
        await message.answer(error_text(exc))


@router.callback_query(F.data.startswith("credit:admin:view:"))
async def admin_view(callback: CallbackQuery, state: FSMContext):
    if not await admin_allowed(callback):
        return
    try:
        _, _, _, target, currency, page = callback.data.split(":")
        await show_admin(callback.message, callback.from_user.id, int(target), currency, state, max(1, int(page)))
        await callback.answer()
    except (ValueError, BackendAPIError) as exc:
        await callback.answer(error_text(exc), show_alert=True)


async def ask_reason(message, state):
    await state.set_state(CreditStates.reason)
    await message.answer(tr("دلیل داخلی این تغییر را بنویسید (۵ تا ۵۰۰ حرف). دلیل برای کاربر ارسال نمی‌شود.",
                            "Enter an internal reason (5–500 characters). It is not sent to the customer."))


@router.callback_query(F.data.startswith("credit:adjust:"))
async def adjust(callback: CallbackQuery, state: FSMContext):
    if not await admin_allowed(callback):
        return
    try:
        _, _, action, target, currency = callback.data.split(":")
        if action not in {"credit", "debit"} or currency not in {"IRT", "USDT"}:
            raise ValueError()
        account = await _payment_request("GET", f"/admin/credit/{int(target)}?actor_telegram_id={callback.from_user.id}&currency={currency}")
        await state.clear()
        await state.update_data(credit_target=int(target), credit_currency=currency, credit_account=account,
            request_id=str(uuid.uuid4()), action=action, expected_version=account["version"])
        await state.set_state(CreditStates.amount)
        await callback.message.answer(tr("مبلغ مثبت را وارد کنید؛ تومان عدد صحیح و USDT حداکثر ۴ رقم اعشار.",
            "Enter a positive amount: whole IRT or up to 4 decimal places for USDT."),
            reply_markup=keyboard([[(tr("انصراف", "Cancel"), f"credit:admin:view:{target}:{currency}:1")]]))
        await callback.answer()
    except (ValueError, BackendAPIError) as exc:
        await callback.answer(error_text(exc), show_alert=True)


@router.callback_query(CreditStates.view, F.data.startswith("credit:reverse:"))
async def reverse(callback: CallbackQuery, state: FSMContext):
    if not await admin_allowed(callback):
        return
    data = await state.get_data()
    entry_id = callback.data.rsplit(":", 1)[1]
    item = next((r for r in data["credit_account"]["items"] if r["id"] == entry_id), None)
    if item is None or item["kind"] != "adjustment" or item["reversed"]:
        return await callback.answer(tr("حساب را دوباره باز کنید.", "Reopen the account."), show_alert=True)
    await state.update_data(request_id=str(uuid.uuid4()), action="reverse", entry_id=entry_id,
                           expected_version=data["credit_account"]["version"], delta=str(-Decimal(item["delta"])))
    await ask_reason(callback.message, state)
    await callback.answer()


@router.message(CreditStates.amount, SupportFormInput(), F.text)
async def receive_amount(message: Message, state: FSMContext):
    if not await admin_allowed(message):
        return
    data = await state.get_data()
    try:
        value = message.text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬", "0123456789.,")).strip().replace(",", "")
        amount = Decimal(value)
        if not amount.is_finite() or not 0 < amount <= Decimal("99999999999999.9999"):
            raise ValueError()
        unit = Decimal("1") if data["credit_currency"] == "IRT" else Decimal("0.0001")
        if amount != amount.quantize(unit):
            raise ValueError()
        await state.update_data(amount=str(amount), delta=str(amount if data["action"] == "credit" else -amount))
        await ask_reason(message, state)
    except (InvalidOperation, ValueError):
        await message.answer(tr("مبلغ معتبر نیست؛ تومان بدون اعشار و USDT حداکثر ۴ رقم اعشار.",
                                "Invalid amount: whole IRT or up to 4 decimal places for USDT."))


@router.message(CreditStates.reason, SupportFormInput(), F.text)
async def receive_reason(message: Message, state: FSMContext):
    if not await admin_allowed(message):
        return
    reason = message.text.strip()
    if not 5 <= len(reason) <= 500:
        return await message.answer(tr("دلیل باید ۵ تا ۵۰۰ حرف باشد.", "Use 5–500 characters."))
    data = await state.get_data()
    after = Decimal(data["credit_account"]["balance"]) + Decimal(data["delta"])
    if after < 0:
        return await message.answer(tr("این تغییر موجودی را منفی می‌کند؛ مبلغ را دوباره انتخاب کنید.", "This would make the balance negative. Choose another amount."),
            reply_markup=keyboard([[(tr("بازگشت به حساب", "Back to account"), f"credit:admin:view:{data['credit_target']}:{data['credit_currency']}:1")]]))
    await state.update_data(reason=reason)
    await state.set_state(CreditStates.confirm)
    sign = "+" if Decimal(data["delta"]) > 0 else ""
    text = (f"{tr('تأیید تغییر اعتبار', 'Confirm credit adjustment')}\nTelegram ID: <code>{data['credit_target']}</code>\n"
            f"{money(data['credit_account']['balance'], data['credit_currency'])} → {money(after, data['credit_currency'])}\n"
            f"{tr('تغییر', 'Change')}: {sign}{money(data['delta'], data['credit_currency'])}\n{html.escape(reason)}")
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard([
        [(tr("✅ تأیید نهایی", "✅ Confirm"), "credit:admin:confirm")],
        [(tr("انصراف", "Cancel"), f"credit:admin:view:{data['credit_target']}:{data['credit_currency']}:1")]]))


@router.callback_query(CreditStates.confirm, F.data == "credit:admin:confirm")
async def confirm_adjustment(callback: CallbackQuery, state: FSMContext):
    if not await admin_allowed(callback):
        return
    data = await state.get_data()
    try:
        payload = {key: data[key] for key in ("request_id", "expected_version", "action", "reason", "amount", "entry_id") if key in data}
        await _payment_request("POST", f"/admin/credit/{data['credit_target']}", payload={
            "actor_telegram_id": callback.from_user.id, "currency": data["credit_currency"], **payload})
        await callback.message.answer(tr("✅ تغییر ثبت شد. وضعیت اطلاع‌رسانی در گردش حساب دیده می‌شود.",
                                         "✅ Adjustment saved. Notification status is shown in the ledger."))
        await callback.answer()
    except BackendAPIError as exc:
        return await callback.answer(error_text(exc), show_alert=True)
    try:
        await show_admin(callback.message, callback.from_user.id, data["credit_target"], data["credit_currency"], state)
    except BackendAPIError:
        await state.clear()


async def preview_purchase(message, state, actor, order):
    if message.chat.type != "private":
        raise BackendAPIError(status_code=403, detail={"code": "credit_private_required"})
    user = await get_telegram_user(actor)
    language = "en" if user.get("effective_language") == "en" else "fa"
    currency = "USDT" if language == "en" else "IRT"
    if order["status"] != "open" or order["destination"].get("type") != "credit":
        raise BackendAPIError(status_code=409, detail={"code": "credit_order_closed"})
    if order["currency"] != currency:
        raise BackendAPIError(status_code=403, detail={"code": "payment_order_language"})
    account = await _payment_request("GET", f"/credit/me?telegram_id={actor}")
    from app.keyboards.payment import build_receipt_cancel_keyboard
    rows = []
    enough = Decimal(account["balance"]) >= Decimal(order["offer"]["price"])
    await state.clear()
    await state.set_state(CreditStates.purchase)
    await state.update_data(order_id=str(order["id"]))
    if enough:
        rows.append([(tr("✅ تأیید خرید با اعتبار", "✅ Confirm credit purchase"), "credit:pay:confirm")])
    else:
        rows.append([(tr("موجودی و گردش اعتبار", "Balance and history"), "credit:mine:1")])
    markup = keyboard(rows)
    markup.inline_keyboard += build_receipt_cancel_keyboard(language, str(order["id"])).inline_keyboard
    offer = order["offer"]
    text = (f"{tr('💰 خرید با اعتبار داخلی', '💰 Pay with internal credit')}\n<b>{html.escape(offer['label'])}</b>\n"
            f"{tr('مدت (روز)', 'Duration (days)')}: {offer['duration_days']}\n"
            f"{tr('مبلغ', 'Amount')}: {money(offer['price'], currency)}\n"
            f"{tr('موجودی', 'Balance')}: {money(account['balance'], currency)}\n")
    text += tr("با تأیید، کل مبلغ کسر و اشتراک فعال یا طبق دوره‌های فعلی تمدید/زمان‌بندی می‌شود.",
               "Confirming deducts the full amount and activates, renews or schedules your subscription based on existing periods.") if enough else error_text(BackendAPIError(status_code=409, detail={"code": "credit_insufficient"}))
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "credit:pay:start")
async def start_purchase(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        user = await get_telegram_user(callback.from_user.id)
        currency = "USDT" if user.get("effective_language") == "en" else "IRT"
        order = await _payment_request("POST", "/payments/orders", payload={"telegram_id": callback.from_user.id,
            "offer_code": data["offer"]["code"], "currency": currency, "method": "credit"})
        await preview_purchase(callback.message, state, callback.from_user.id, order)
        await callback.answer()
    except (KeyError, BackendAPIError) as exc:
        await callback.answer(error_text(exc), show_alert=True)


@router.callback_query(CreditStates.purchase, F.data == "credit:pay:confirm")
async def pay(callback: CallbackQuery, state: FSMContext):
    try:
        order_id = (await state.get_data())["order_id"]
        result = await _payment_request("POST", f"/credit/orders/{order_id}/pay", payload={"telegram_id": callback.from_user.id})
        await state.clear()
        await callback.message.answer(tr("✅ خرید با اعتبار ثبت شد. وضعیت و زمان شروع اشتراک را از «اشتراک من» ببینید.",
            "✅ Credit purchase completed. View your subscription and its start date in My subscription.") +
            f"\n{tr('موجودی فعلی', 'Current balance')}: {money(result['account']['balance'], result['account']['currency'])}",
            reply_markup=keyboard([[(tr("اشتراک من", "My subscription"), "payment:status"),
                                    (tr("گردش اعتبار", "Credit history"), "credit:mine:1")]]))
        await callback.answer()
    except (KeyError, BackendAPIError) as exc:
        await callback.answer(error_text(exc), show_alert=True)
