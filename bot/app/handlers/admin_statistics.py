import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin_statistics import build_statistics_home_keyboard, build_statistics_section_keyboard
from app.services.backend import BackendAPIError, get_admin_statistics, get_admin_context


router = Router(name="admin-statistics")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


def _can(context: dict) -> bool:
    return bool(context.get("is_superadmin") or "statistics.view" in set(context.get("permissions", [])))


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return html.escape(str(value or "—"))


def _money(value: object) -> str:
    return f"{_fmt(value)} تومان"


def _section_text(section: str, data: dict, period: str) -> str:
    if section == "overview":
        d = data.get("overview", {})
        return "📊 <b>آمار کلی ربات</b>\n\n" + "\n".join([
            f"👥 کل کاربران: <code>{_fmt(d.get('total_users'))}</code>", f"🟢 کاربران فعال: <code>{_fmt(d.get('active_users'))}</code>", f"🆕 کاربران جدید امروز: <code>{_fmt(d.get('new_users_today'))}</code>", f"📅 کاربران جدید این هفته: <code>{_fmt(d.get('new_users_7d'))}</code>", f"🗓️ کاربران جدید این ماه: <code>{_fmt(d.get('new_users_30d'))}</code>", f"💎 اشتراک فعال: <code>{_fmt(d.get('active_subscriptions'))}</code>", f"⏰ اشتراک منقضی: <code>{_fmt(d.get('expired_subscriptions'))}</code>", f"💰 کل فروش: <code>{_money(d.get('total_sales_irt'))}</code>", f"💳 تعداد خریدها: <code>{_fmt(d.get('purchase_count'))}</code>", f"📥 تعداد دانلودها: <code>{_fmt(d.get('total_downloads'))}</code>"])
    d = data.get(section, {})
    titles = {"users": "👥 آمار کاربران", "subscriptions": "💎 آمار اشتراک", "finance": "💰 آمار مالی", "downloads": "📥 آمار استفاده از ربات", "kpis": "🎯 شاخص‌های مدیریتی"}
    if section == "users":
        lines = [f"{titles[section]}", "", f"امروز: {_fmt(d.get('new_today'))}", f"دیروز: {_fmt(d.get('new_yesterday'))}", f"۷ روز اخیر: {_fmt(d.get('new_7d'))}", f"۳۰ روز اخیر: {_fmt(d.get('new_30d'))}", f"این ماه: {_fmt(d.get('new_this_month'))}", f"ماه گذشته: {_fmt(d.get('new_previous_month'))}", f"کل: {_fmt(d.get('new_all'))}", "", f"📈 نرخ رشد: {_fmt(d.get('growth_rate'))}%", f"🔄 کاربران بازگشتی: {_fmt(d.get('returning_users'))}", f"DAU: {_fmt(d.get('dau'))} | WAU: {_fmt(d.get('wau'))} | MAU: {_fmt(d.get('mau'))}"]
    elif section == "subscriptions":
        lines = [titles[section], "", f"کل فروخته‌شده: {_fmt(d.get('total_sold'))}", f"🟢 فعال: {_fmt(d.get('active'))}", f"⏰ منقضی: {_fmt(d.get('expired'))}", f"🚫 لغوشده: {_fmt(d.get('cancelled'))}", f"🔄 تمدیدشده: {_fmt(d.get('renewed'))}", f"🆕 امروز: {_fmt(d.get('new_today'))}", f"📅 این هفته: {_fmt(d.get('new_7d'))}", f"🗓️ این ماه: {_fmt(d.get('new_30d'))}", "", "بر اساس پلن:"] + [f"• {html.escape(str(row.get('name') or '—'))}: {_fmt(row.get('count'))}" for row in d.get('by_plan', [])]
    elif section == "finance":
        rev = d.get("revenue", {})
        lines = [titles[section], "", f"امروز: {_money(rev.get('today', {}).get('irt'))}", f"۷ روز اخیر: {_money(rev.get('7d', {}).get('irt'))}", f"۳۰ روز اخیر: {_money(rev.get('30d', {}).get('irt'))}", f"این ماه: {_money(rev.get('this_month', {}).get('irt'))}", f"ماه گذشته: {_money(rev.get('previous_month', {}).get('irt'))}", f"کل درآمد: {_money(rev.get('all', {}).get('irt'))}", f"🧾 تراکنش موفق: {_fmt(d.get('successful_transactions'))}", f"❌ تراکنش ناموفق: {_fmt(d.get('failed_transactions'))}", f"💳 میانگین خرید: {_money(d.get('average_purchase_irt'))}", f"🔄 درآمد تمدید: {_money(d.get('renewal_revenue_irt'))}", f"🆕 درآمد خرید اولیه: {_money(d.get('initial_revenue_irt'))}"]
    elif section == "downloads":
        lines = [titles[section], "", f"کل دانلودها: {_fmt(d.get('total'))}", f"امروز: {_fmt(d.get('today'))}", f"این هفته: {_fmt(d.get('7d'))}", f"این ماه: {_fmt(d.get('30d'))}", f"✅ موفق: {_fmt(d.get('successful'))}", f"❌ ناموفق: {_fmt(d.get('failed'))}", f"🔗 لینک پردازش‌شده: {_fmt(d.get('processed_links'))}", f"💾 حجم فایل‌ها: {_fmt(d.get('total_file_size'))} bytes", "", "بر اساس سایت:"] + [f"• {html.escape(str(row.get('site')))}: {_fmt(row.get('count'))}" for row in d.get('by_site', [])]
    elif section == "kpis":
        lines = [titles[section], "", f"Conversion Rate: {_fmt(d.get('conversion_rate'))}%", f"Retention: {_fmt(d.get('retention'))}%", f"Renewal Rate: {_fmt(d.get('renewal_rate'))}%", f"Churn Rate: {_fmt(d.get('churn_rate'))}%", f"ARPU: {_money(d.get('arpu_irt'))}", f"ARPPU: {_money(d.get('arppu_irt'))}", f"LTV (approx.): {_money(d.get('ltv_irt'))}"]
    else:
        chart = data.get("charts", {})
        lines = ["📈 <b>روند رشد</b>", f"معیار: {html.escape(str(chart.get('metric') or 'users'))}", f"بازه: {html.escape(str(chart.get('range') or period))}", ""]
        points = chart.get("points", [])
        peak = max((int(p.get('count') or 0) for p in points), default=1)
        for point in points:
            count = int(point.get("count") or 0)
            bars = "▇" * min(18, round(count / peak * 18) if peak else 0)
            lines.append(f"{point.get('date')}: {bars} <code>{count}</code>")
    return "\n".join(lines)


async def _show(callback: CallbackQuery, section: str, *, period: str = "30d", chart_range: str = "30d", metric: str = "users") -> None:
    context = await get_admin_context(callback.from_user.id)
    if not context.get("is_admin") or not _can(context):
        await callback.answer("دسترسی مشاهده آمار را ندارید.", show_alert=True)
        return
    data = await get_admin_statistics(callback.from_user.id, section=section, period=period, chart_range=chart_range, metric=metric)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(_section_text(section, data, period), parse_mode="HTML", reply_markup=build_statistics_section_keyboard(section))
    await callback.answer()


@router.callback_query(F.data == "admin:statistics")
async def statistics_home(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message): return
    try:
        context = await get_admin_context(callback.from_user.id)
        if not context.get("is_admin") or not _can(context):
            await callback.answer("دسترسی مشاهده آمار را ندارید.", show_alert=True); return
        await state.clear()
        data = await get_admin_statistics(callback.from_user.id, section="overview")
        await callback.message.edit_text(_section_text("overview", data, "30d"), parse_mode="HTML", reply_markup=build_statistics_home_keyboard())
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer("دریافت آمار ممکن نشد.", show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:stats:(users|subscriptions|finance|downloads|kpis)(?::(today|7d|30d|all))?$"))
async def statistics_section(callback: CallbackQuery) -> None:
    if not callback.data: return
    parts = callback.data.split(":")
    await _show(callback, parts[2], period=parts[3] if len(parts) > 3 else "30d")


@router.callback_query(F.data.startswith("admin:stats:charts"))
async def statistics_charts(callback: CallbackQuery) -> None:
    parts = callback.data.split(":") if callback.data else []
    chart_range, metric = "30d", "users"
    if len(parts) == 4 and parts[3] in {"today", "7d", "30d", "3mo", "1yr"}: chart_range = parts[3]
    if len(parts) == 5 and parts[3] == "metric": metric = parts[4]
    await _show(callback, "charts", chart_range=chart_range, metric=metric)
