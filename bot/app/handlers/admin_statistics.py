from app.localization import tr as _tr, localized_collection as _localized_collection
import html
from decimal import Decimal
from aiogram.exceptions import TelegramBadRequest

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin_statistics import build_statistics_home_keyboard, build_statistics_section_keyboard, STATISTICS_PERIODS
from app.services.backend import BackendAPIError, get_admin_statistics, get_admin_context
from app.middleware.interface import ui_language


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
    return f"{_fmt(value)}{_tr(' تومان')}"


def _section_text(section: str, data: dict, period: str) -> str:
    if section == "overview":
        d = data.get("overview", {})
        return _tr("📊 <b>آمار کلی ربات</b>\n\n") + "\n".join([
            f"{_tr('👥 کل کاربران: <code>')}{_fmt(d.get('total_users'))}</code>", f"{_tr('🟢 کاربران فعال: <code>')}{_fmt(d.get('active_users'))}</code>", f"{_tr('🆕 کاربران جدید امروز: <code>')}{_fmt(d.get('new_users_today'))}</code>", f"{_tr('📅 کاربران جدید این هفته: <code>')}{_fmt(d.get('new_users_7d'))}</code>", f"{_tr('🗓️ کاربران جدید این ماه: <code>')}{_fmt(d.get('new_users_30d'))}</code>", f"{_tr('💎 اشتراک فعال: <code>')}{_fmt(d.get('active_subscriptions'))}</code>", f"{_tr('⏰ اشتراک منقضی: <code>')}{_fmt(d.get('expired_subscriptions'))}</code>", f"{_tr('💰 کل فروش: <code>')}{_money(d.get('total_sales_irt'))}</code>", f"{_tr('💳 تعداد خریدها: <code>')}{_fmt(d.get('purchase_count'))}</code>", f"{_tr('📥 تعداد دانلودها: <code>')}{_fmt(d.get('total_downloads'))}</code>"])
    d = data.get(section, {})
    titles = {"users": _tr("👥 آمار کاربران"), "subscriptions": _tr("💎 آمار اشتراک"), "finance": _tr("💰 آمار مالی"), "downloads": _tr("📥 آمار استفاده از ربات"), "kpis": _tr("🎯 شاخص‌های مدیریتی")}
    if section == "users":
        lines = [f"{titles[section]}", "", f"{_tr('امروز: ')}{_fmt(d.get('new_today'))}", f"{_tr('دیروز: ')}{_fmt(d.get('new_yesterday'))}", f"{_tr('۷ روز اخیر: ')}{_fmt(d.get('new_7d'))}", f"{_tr('۳۰ روز اخیر: ')}{_fmt(d.get('new_30d'))}", f"{_tr('این ماه: ')}{_fmt(d.get('new_this_month'))}", f"{_tr('ماه گذشته: ')}{_fmt(d.get('new_previous_month'))}", f"{_tr('کل: ')}{_fmt(d.get('new_all'))}", "", f"{_tr('📈 نرخ رشد: ')}{_fmt(d.get('growth_rate'))}%", f"{_tr('🔄 کاربران بازگشتی: ')}{_fmt(d.get('returning_users'))}", f"DAU: {_fmt(d.get('dau'))} | WAU: {_fmt(d.get('wau'))} | MAU: {_fmt(d.get('mau'))}"]
    elif section == "subscriptions":
        def plan_label(row: dict) -> str:
            if ui_language.get() == "en":
                value = str(row.get("name_en") or "").strip()
                return value if value and not any("\u0600" <= char <= "\u06ff" for char in value) else "Plan"
            return str(row.get("name") or "—")

        lines = [titles[section], "", f"{_tr('کل فروخته\u200cشده: ')}{_fmt(d.get('total_sold'))}", f"{_tr('🟢 فعال: ')}{_fmt(d.get('active'))}", f"{_tr('⏰ منقضی: ')}{_fmt(d.get('expired'))}", f"{_tr('🚫 لغوشده: ')}{_fmt(d.get('cancelled'))}", f"{_tr('🔄 تمدیدشده: ')}{_fmt(d.get('renewed'))}", f"{_tr('🆕 امروز: ')}{_fmt(d.get('new_today'))}", f"{_tr('📅 این هفته: ')}{_fmt(d.get('new_7d'))}", f"{_tr('🗓️ این ماه: ')}{_fmt(d.get('new_30d'))}", "", _tr("بر اساس پلن:")] + [f"• {html.escape(plan_label(row))}: {_fmt(row.get('count'))}" for row in d.get('by_plan', [])]
    elif section == "finance":
        label = dict(STATISTICS_PERIODS).get(period, period)
        lines = [titles[section], f"{_tr('بازه: ')}{label}", f"{d.get('start', '')[:10]} → {d.get('end', '')[:10]}", ""]
        for currency, title in (("IRT", _tr("💳 درآمد ریالی (تومان)")), ("USDT", _tr("💵 درآمد USDT"))):
            values = (d.get("currencies") or {}).get(currency, {})
            def money(key):
                value = Decimal(str(values.get(key) or 0))
                return f"{value:,.0f} Toman" if currency == "IRT" else f"{value:,.4f} USDT"
            lines.extend([title,
                f"{_tr('💰 درآمد تأییدشده: ')}{money('total')}",
                f"{_tr('✅ تراکنش موفق: ')}{_fmt(values.get('successful'))}",
                f"{_tr('❌ تراکنش ردشده: ')}{_fmt(values.get('rejected'))}",
                f"{_tr('⏳ در انتظار: ')}{_fmt(values.get('pending'))}",
                f"{_tr('💳 میانگین خرید: ')}{money('average')}",
                f"{_tr('🔄 درآمد تمدید: ')}{money('renewal')}",
                f"{_tr('🆕 درآمد سایر خریدها: ')}{money('initial')}", ""])
        lines.append(_tr("درآمد بر اساس زمان تأیید پرداخت محاسبه می‌شود."))
    elif section == "downloads":
        label = dict(STATISTICS_PERIODS).get(period, period)
        lines = [titles[section], f"{_tr('بازه: ')}{label}", f"{d.get('start', '')[:10]} → {d.get('end', '')[:10]}", "",
            f"{_tr('کل درخواست\u200cها: ')}{_fmt(d.get('total'))}",
            f"{_tr('✅ فایل دانلودشده: ')}{_fmt(d.get('successful'))}", f"{_tr('❌ ناموفق: ')}{_fmt(d.get('failed'))}",
            f"{_tr('🔗 لینک پردازش\u200cشده: ')}{_fmt(d.get('processed_links'))}",
            f"{_tr('💾 حجم فایل\u200cها: ')}{_fmt(d.get('total_file_size'))} bytes", "", _tr("تعداد فایل دانلودشده از هر سایت:")]
        lines += [f"• {html.escape(str(row.get('site')))}: {_fmt(row.get('count'))}" for row in d.get('by_site', [])]
        if not d.get('by_site'): lines.append(_tr("در این بازه فایلی دانلود نشده است."))
        if int(d.get('site_total') or 0) > 8:
            lines.append(f"{d.get('page', 1)}/{(int(d['site_total']) + 7) // 8}")
    elif section == "kpis":
        lines = [titles[section], "", f"Conversion Rate: {_fmt(d.get('conversion_rate'))}%", f"Retention: {_fmt(d.get('retention'))}%", f"Renewal Rate: {_fmt(d.get('renewal_rate'))}%", f"Churn Rate: {_fmt(d.get('churn_rate'))}%", f"ARPU: {_money(d.get('arpu_irt'))}", f"ARPPU: {_money(d.get('arppu_irt'))}", f"LTV (approx.): {_money(d.get('ltv_irt'))}"]
    else:
        chart = data.get("charts", {})
        lines = [_tr("📈 <b>روند رشد</b>"), f"{_tr('معیار: ')}{html.escape(str(chart.get('metric') or 'users'))}", f"{_tr('بازه: ')}{html.escape(str(chart.get('range') or period))}", ""]
        points = chart.get("points", [])
        peak = max((int(p.get('count') or 0) for p in points), default=1)
        for point in points:
            count = int(point.get("count") or 0)
            bars = "▇" * min(18, round(count / peak * 18) if peak else 0)
            lines.append(f"{point.get('date')}: {bars} <code>{count}</code>")
    return "\n".join(lines)


async def _show(callback: CallbackQuery, section: str, *, period: str = "1mo", chart_range: str = "1mo", metric: str = "users", page: int = 1) -> None:
    try:
        context = await get_admin_context(callback.from_user.id)
        if not context.get("is_admin") or not _can(context):
            await callback.answer(_tr("دسترسی مشاهده آمار را ندارید."), show_alert=True)
            return
        data = await get_admin_statistics(callback.from_user.id, section=section, period=period, chart_range=chart_range, metric=metric, page=page)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(_section_text(section, data, period), parse_mode="HTML",
                reply_markup=build_statistics_section_keyboard(section, period=period, page=page, total=(data.get("downloads") or {}).get("site_total", 0)))
        await callback.answer()
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            await callback.answer(_tr("آمار به‌روز است."))
        else:
            await callback.answer(_tr("نمایش آمار انجام نشد؛ دوباره تلاش کنید."), show_alert=True)
    except BackendAPIError:
        await callback.answer(_tr("دریافت آمار ممکن نشد."), show_alert=True)


@router.callback_query(F.data == "admin:statistics")
async def statistics_home(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message): return
    try:
        context = await get_admin_context(callback.from_user.id)
        if not context.get("is_admin") or not _can(context):
            await callback.answer(_tr("دسترسی مشاهده آمار را ندارید."), show_alert=True); return
        await state.clear()
        data = await get_admin_statistics(callback.from_user.id, section="overview")
        await callback.message.edit_text(_section_text("overview", data, "1mo"), parse_mode="HTML", reply_markup=build_statistics_home_keyboard())
        await callback.answer()
    except BackendAPIError as exc:
        await callback.answer(_tr("دریافت آمار ممکن نشد."), show_alert=True)


@router.callback_query(F.data.regexp(r"^admin:stats:(users|subscriptions|finance|downloads|kpis)(?::(today|7d|30d|month|1mo|3mo|6mo|1yr|all)(?::([0-9]+))?)?$"))
async def statistics_section(callback: CallbackQuery) -> None:
    if not callback.data: return
    parts = callback.data.split(":")
    await _show(callback, parts[2], period=parts[3] if len(parts) > 3 else "1mo", page=max(1, int(parts[4])) if len(parts) > 4 else 1)


@router.callback_query(F.data.startswith("admin:stats:charts"))
async def statistics_charts(callback: CallbackQuery) -> None:
    parts = callback.data.split(":") if callback.data else []
    chart_range, metric = "1mo", "users"
    if len(parts) == 4 and parts[3] in {"today", "7d", "1mo", "3mo", "6mo", "1yr"}: chart_range = parts[3]
    if len(parts) >= 5 and parts[3] == "metric":
        metric = parts[4]
        if len(parts) == 6 and parts[5] in {"today", "7d", "1mo", "3mo", "6mo", "1yr"}:
            chart_range = parts[5]
    await _show(callback, "charts", period=chart_range, chart_range=chart_range, metric=metric)
