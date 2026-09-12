"""RBAC protected financial reporting and append-only audit browsing."""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.services.admin_access import AdminAccessService, AdminAccessDenied
from app.services.audit import AuditService, sanitize_audit_details
from app.services.managed_settings import get_managed_setting

router = APIRouter(prefix="/admin/reports", dependencies=[Depends(require_internal_api_key)], tags=["reports"])


def period_bounds(period, start, end, timezone_name, now=None):
    now = now or datetime.now(timezone.utc)
    zone = ZoneInfo(timezone_name)
    today = now.astimezone(zone).date()
    if period in {"today", "7d", "1mo", "3mo", "6mo", "1yr"}:
        from app.services.admin_statistics import _period_start
        return _period_start(now.astimezone(zone), period).astimezone(timezone.utc), now
    if period == "daily":
        start, end = today, today
    elif period == "weekly":
        start, end = today - timedelta(days=6), today
    elif period == "monthly":
        start, end = today.replace(day=1), today
    elif period == "yearly":
        start, end = today.replace(month=1, day=1), today
    elif period == "all":
        return None, None
    elif period != "custom" or start is None or end is None:
        raise ValueError("Custom range requires start and end dates (YYYY-MM-DD)")
    if end < start:
        raise ValueError("End date must be on or after start date")
    return (datetime.combine(start, datetime.min.time(), zone).astimezone(timezone.utc),
            datetime.combine(end + timedelta(days=1), datetime.min.time(), zone).astimezone(timezone.utc))


async def report_context(db, actor_id, permission, period, start, end):
    try:
        context = await AdminAccessService(db).require_permission(actor_id, permission)
    except AdminAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    tz = await get_managed_setting(db, "quota.timezone")
    try:
        lower, upper = period_bounds(period, start, end, tz)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return context, tz, lower, upper


def payment_filters(lower, upper):
    # Successful and rejected operations use the actual review time; pending
    # payments use submission time. All breakdowns and exports share this rule.
    timestamp = func.coalesce(Payment.reviewed_at, Payment.created_at)
    filters = []
    if lower is not None:
        filters += [timestamp >= lower, timestamp < upper]
    return filters


def currency_expression():
    return case((Payment.payment_method == "usdt", "USDT"), else_="IRT")


@router.get("/finance")
async def finance(actor_telegram_id: int = Query(gt=0), period: str = "monthly",
                  start: date | None = None, end: date | None = None,
                  page: int = Query(default=1, ge=1), page_size: int = Query(default=8, ge=1, le=30),
                  db: AsyncSession = Depends(get_db)):
    _, tz, lower, upper = await report_context(db, actor_telegram_id, "payments.view", period, start, end)
    filters = payment_filters(lower, upper)
    currency = currency_expression()
    totals = (await db.execute(select(currency.label("currency"), Payment.status,
        func.count(Payment.id).label("count"), func.sum(Payment.amount).label("amount")
    ).where(*filters).group_by(currency, Payment.status))).all()
    group = [Payment.plan_name_snapshot, Payment.duration_days, Payment.payment_method, currency, Payment.status]
    query = select(*group, func.count(Payment.id), func.sum(Payment.amount)).where(*filters).group_by(*group)
    total_groups = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = (await db.execute(query.order_by(*group).offset((page - 1) * page_size).limit(page_size))).all()
    changes = (await db.execute(select(Payment.subscription_change_type, currency,
        func.count(Payment.id)).where(*filters, Payment.status == PaymentStatus.APPROVED)
        .group_by(Payment.subscription_change_type, currency))).all()
    return {"timezone": tz, "start": lower, "end_exclusive": upper,
        "basis": "reviewed_at for reviewed payments; created_at for pending payments",
        "totals": [{"currency": r[0], "status": r[1].value, "count": r[2], "amount": str(r[3])} for r in totals],
        "items": [{"plan": r[0], "duration_days": r[1], "method": r[2], "currency": r[3],
                   "status": r[4].value, "count": r[5], "amount": str(r[6])} for r in rows],
        "changes": [{"type": r[0] or "legacy", "currency": r[1], "count": r[2]} for r in changes],
        "total": total_groups, "page": page, "page_size": page_size}


def csv_cell(value):
    text = "" if value is None else str(value)
    # Prevent spreadsheet formula execution in exported user-controlled text.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


@router.get("/finance.csv")
async def finance_csv(actor_telegram_id: int = Query(gt=0), period: str = "monthly",
                      start: date | None = None, end: date | None = None,
                      db: AsyncSession = Depends(get_db)):
    context, tz, lower, upper = await report_context(db, actor_telegram_id, "payments.view", period, start, end)
    filters = payment_filters(lower, upper)
    count = int((await db.execute(select(func.count(Payment.id)).where(*filters))).scalar_one())
    if count > 10000:
        raise HTTPException(422, "This export exceeds 10,000 rows; select a narrower date range")
    rows = (await db.execute(select(Payment, User.telegram_id).join(User, User.id == Payment.user_id)
        .where(*filters).order_by(Payment.id))).all()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["payment_id", "telegram_id", "plan", "duration_days", "method", "currency", "amount",
        "status", "change_type", "created_at_utc", "reviewed_at_utc", "reviewed_by_telegram_id"])
    for payment, telegram_id in rows:
        writer.writerow([csv_cell(x) for x in [payment.id, telegram_id, payment.plan_name_snapshot,
            payment.duration_days, payment.payment_method, "USDT" if payment.payment_method == "usdt" else "IRT",
            payment.amount, payment.status.value, payment.subscription_change_type,
            payment.created_at.isoformat(), payment.reviewed_at.isoformat() if payment.reviewed_at else None,
            payment.reviewed_by_telegram_id]])
    AuditService(db).record(action="finance.csv_exported", actor_user_id=context.user_id,
        actor_telegram_id=actor_telegram_id, target_type="financial_report", details={"rows": count, "period": period})
    await db.commit()
    return Response(stream.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mediahub-finance.csv"'})


@router.get("/audit")
async def audit(actor_telegram_id: int = Query(gt=0), page: int = Query(default=1, ge=1),
                page_size: int = Query(default=8, ge=1, le=30),
                filter_actor: int | None = Query(default=None, gt=0), action: str | None = Query(default=None, max_length=120),
                period: str = "monthly", start: date | None = None, end: date | None = None,
                db: AsyncSession = Depends(get_db)):
    _, tz, lower, upper = await report_context(db, actor_telegram_id, "audit.view", period, start, end)
    filters = []
    if lower:
        filters += [AuditLog.created_at >= lower, AuditLog.created_at < upper]
    if filter_actor:
        filters.append(AuditLog.actor_telegram_id == filter_actor)
    if action:
        filters.append(AuditLog.action == action)
    total = int((await db.execute(select(func.count(AuditLog.id)).where(*filters))).scalar_one())
    rows = (await db.execute(select(AuditLog).where(*filters).order_by(AuditLog.id.desc())
        .offset((page-1)*page_size).limit(page_size))).scalars()
    activity = (await db.execute(select(AuditLog.actor_telegram_id, func.count(AuditLog.id),
        func.count(AuditLog.id).filter(AuditLog.success.is_(False))).where(*filters, AuditLog.actor_telegram_id.is_not(None),
        ~AuditLog.action.in_(("support.ticket_created", "user.language_changed")))
        .group_by(AuditLog.actor_telegram_id).order_by(func.count(AuditLog.id).desc(), AuditLog.actor_telegram_id)
        .offset((page-1)*page_size).limit(page_size))).all()
    return {"items": [{"id": r.id, "actor": r.actor_telegram_id, "action": r.action,
        "target_type": r.target_type, "target_id": r.target_id, "success": r.success,
        "details": sanitize_audit_details(r.details), "created_at": r.created_at} for r in rows],
        "activity": [{"actor": r[0], "actions": r[1], "failed": r[2]} for r in activity],
        "total": total, "page": page, "page_size": page_size, "timezone": tz}
