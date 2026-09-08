from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.models.payment import Payment, PaymentStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus


def _timezone() -> ZoneInfo:
    value = getattr(settings, "quota_timezone", None) or "Asia/Tehran"
    try:
        return ZoneInfo(value)
    except Exception:
        return ZoneInfo("Asia/Tehran")


def _period_start(now: datetime, period: str) -> datetime:
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period in {"7d", "week"}:
        return now - timedelta(days=7)
    if period in {"30d", "month"}:
        return now - timedelta(days=30)
    return datetime(1970, 1, 1, tzinfo=now.tzinfo)


def _json_number(value: Any) -> int | float:
    if isinstance(value, Decimal):
        return float(value)
    return int(value or 0)


class AdminStatisticsService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.tz = _timezone()
        self.now = datetime.now(timezone.utc).astimezone(self.tz)

    async def _scalar(self, statement) -> Any:
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def _count_users(self, where=()) -> int:
        statement = select(func.count(User.id)).where(User.status != UserStatus.DELETED, *where)
        return int(await self._scalar(statement) or 0)

    async def _count_created(self, start: datetime, end: datetime | None = None) -> int:
        where = [User.status != UserStatus.DELETED, User.created_at >= start]
        if end is not None:
            where.append(User.created_at < end)
        return await self._count_users(where)

    async def _count_activity(self, start: datetime) -> int:
        return await self._count_users([User.last_activity_at >= start])

    async def overview(self) -> dict[str, Any]:
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = self.now - timedelta(days=7)
        month = self.now - timedelta(days=30)
        total_users = await self._count_users()
        active_subscriptions = int(await self._scalar(select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.ACTIVE, Subscription.expires_at >= self.now)) or 0)
        expired_subscriptions = int(await self._scalar(select(func.count(Subscription.id)).where((Subscription.status == SubscriptionStatus.EXPIRED) | (Subscription.expires_at < self.now))) or 0)
        approved = Payment.status == PaymentStatus.APPROVED
        sales = await self._scalar(select(func.coalesce(func.sum(case((Payment.payment_method == "card", Payment.amount), else_=0)), 0)).where(approved))
        purchases = int(await self._scalar(select(func.count(Payment.id)).where(approved)) or 0)
        downloads = int(await self._scalar(select(func.count(DownloadJob.id))) or 0)
        return {
            "total_users": total_users,
            "active_users": await self._count_users([User.status == UserStatus.ACTIVE]),
            "new_users_today": await self._count_created(today),
            "new_users_7d": await self._count_created(week),
            "new_users_30d": await self._count_created(month),
            "active_subscriptions": active_subscriptions,
            "expired_subscriptions": expired_subscriptions,
            "total_sales_irt": _json_number(sales),
            "purchase_count": purchases,
            "total_downloads": downloads,
        }

    async def users(self) -> dict[str, Any]:
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)
        week = self.now - timedelta(days=7)
        month = self.now - timedelta(days=30)
        first_month = self.now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_month_end = first_month
        previous_month_start = (first_month - timedelta(days=1)).replace(day=1)
        total = await self._count_users()
        returning = await self._count_users([User.created_at < week, User.last_activity_at >= week])
        return {
            "new_today": await self._count_created(today),
            "new_yesterday": await self._count_created(yesterday, today),
            "new_7d": await self._count_created(week),
            "new_30d": await self._count_created(month),
            "new_this_month": await self._count_created(first_month),
            "new_previous_month": await self._count_created(previous_month_start, previous_month_end),
            "new_all": total,
            "growth_rate": round((await self._count_created(week) / max(total - await self._count_created(week), 1)) * 100, 2),
            "returning_users": returning,
            "dau": await self._count_activity(self.now - timedelta(days=1)),
            "wau": await self._count_activity(self.now - timedelta(days=7)),
            "mau": await self._count_activity(self.now - timedelta(days=30)),
        }

    async def subscriptions(self) -> dict[str, Any]:
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = self.now - timedelta(days=7)
        month = self.now - timedelta(days=30)
        total = int(await self._scalar(select(func.count(Subscription.id))) or 0)
        active = int(await self._scalar(select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.ACTIVE, Subscription.expires_at >= self.now)) or 0)
        expired = int(await self._scalar(select(func.count(Subscription.id)).where((Subscription.status == SubscriptionStatus.EXPIRED) | (Subscription.expires_at < self.now))) or 0)
        cancelled = int(await self._scalar(select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.CANCELLED)) or 0)
        approved = Payment.status == PaymentStatus.APPROVED
        renewals = await self._renewal_count()
        by_plan = await self._plan_counts()
        return {
            "total_sold": int(await self._scalar(select(func.count(Payment.id)).where(approved)) or 0),
            "active": active, "expired": expired, "cancelled": cancelled,
            "renewed": renewals,
            "new_today": await self._approved_count(today),
            "new_7d": await self._approved_count(week),
            "new_30d": await self._approved_count(month),
            "by_plan": by_plan,
            "total_records": total,
        }

    async def _approved_count(self, start: datetime) -> int:
        return int(await self._scalar(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.APPROVED, Payment.created_at >= start)) or 0)

    async def _renewal_count(self) -> int:
        # Correlated aliases keep this portable across PostgreSQL versions.
        from sqlalchemy.orm import aliased
        prior = aliased(Payment)
        statement = select(func.count(Payment.id)).where(Payment.status == PaymentStatus.APPROVED, select(prior.id).where(prior.user_id == Payment.user_id, prior.status == PaymentStatus.APPROVED, prior.created_at < Payment.created_at).exists())
        return int(await self._scalar(statement) or 0)

    async def _plan_counts(self) -> list[dict[str, Any]]:
        result = await self.session.execute(select(Plan.name, Plan.name_en, func.count(Payment.id)).join(Payment, Payment.plan_id == Plan.id).where(Payment.status == PaymentStatus.APPROVED).group_by(Plan.id).order_by(func.count(Payment.id).desc()))
        return [{"name": row[0], "name_en": row[1], "count": int(row[2] or 0)} for row in result.all()]

    async def finance(self) -> dict[str, Any]:
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        first_month = self.now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_month_start = (first_month - timedelta(days=1)).replace(day=1)
        previous_month_end = first_month
        periods = {"today": today, "7d": self.now - timedelta(days=7), "30d": self.now - timedelta(days=30), "this_month": first_month, "previous_month": previous_month_start, "all": datetime(1970, 1, 1, tzinfo=self.now.tzinfo)}
        revenue: dict[str, dict[str, float]] = {}
        for name, start in periods.items():
            revenue[name] = await self._revenue(start, previous_month_end if name == "previous_month" else None)
        successful = int(await self._scalar(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.APPROVED)) or 0)
        failed = int(await self._scalar(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.REJECTED)) or 0)
        total_irt = revenue["all"]["irt"]
        paying_users = int(await self._scalar(select(func.count(distinct(Payment.user_id))).where(Payment.status == PaymentStatus.APPROVED, Payment.payment_method != "usdt")) or 0)
        renewal_irt = (await self._revenue_by_renewal())["irt"]
        return {"revenue": revenue, "successful_transactions": successful, "failed_transactions": failed, "average_purchase_irt": round(total_irt / max(successful, 1), 2), "renewal_revenue_irt": renewal_irt, "initial_revenue_irt": round(total_irt - renewal_irt, 2), "sales_by_plan": await self._plan_counts(), "paying_users": paying_users}

    async def _revenue(self, start: datetime, end: datetime | None = None) -> dict[str, float]:
        where = [Payment.status == PaymentStatus.APPROVED, Payment.created_at >= start]
        if end is not None:
            where.append(Payment.created_at < end)
        statement = select(func.coalesce(func.sum(case((Payment.payment_method == "usdt", 0), else_=Payment.amount)), 0), func.coalesce(func.sum(case((Payment.payment_method == "usdt", Payment.amount), else_=0)), 0)).where(*where)
        result = await self.session.execute(statement)
        row = result.one()
        return {"irt": float(row[0] or 0), "usdt": float(row[1] or 0)}

    async def _revenue_by_renewal(self) -> dict[str, float]:
        from sqlalchemy.orm import aliased
        prior = aliased(Payment)
        exists_prior = select(prior.id).where(prior.user_id == Payment.user_id, prior.status == PaymentStatus.APPROVED, prior.created_at < Payment.created_at).exists()
        result = await self.session.execute(select(func.coalesce(func.sum(case((Payment.payment_method == "usdt", 0), else_=Payment.amount)), 0)).where(Payment.status == PaymentStatus.APPROVED, exists_prior))
        return {"irt": float(result.scalar_one() or 0)}

    async def downloads(self) -> dict[str, Any]:
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        total = int(await self._scalar(select(func.count(DownloadJob.id))) or 0)
        failed = int(await self._scalar(select(func.count(DownloadJob.id)).where(DownloadJob.status == DownloadJobStatus.FAILED)) or 0)
        success = int(await self._scalar(select(func.count(DownloadJob.id)).where(DownloadJob.status == DownloadJobStatus.COMPLETED)) or 0)
        volume = await self._scalar(select(func.coalesce(func.sum(DownloadJob.file_size), 0)))
        links = int(await self._scalar(select(func.count(distinct(DownloadJob.source_url)))) or 0)
        periods = {"today": today, "7d": self.now - timedelta(days=7), "30d": self.now - timedelta(days=30), "all": datetime(1970, 1, 1, tzinfo=self.now.tzinfo)}
        counts = {name: int(await self._scalar(select(func.count(DownloadJob.id)).where(DownloadJob.created_at >= start)) or 0) for name, start in periods.items()}
        site_case = case((func.lower(DownloadJob.source_url).like("%youtube%"), "YouTube"), (func.lower(DownloadJob.source_url).like("%instagram%"), "Instagram"), (func.lower(DownloadJob.source_url).like("%tiktok%"), "TikTok"), (func.lower(DownloadJob.source_url).like("%twitter%"), "Twitter/X"), (func.lower(DownloadJob.source_url).like("%facebook%"), "Facebook"), else_="Other")
        result = await self.session.execute(select(site_case, func.count(DownloadJob.id)).group_by(site_case).order_by(func.count(DownloadJob.id).desc()))
        return {"total": total, "today": counts["today"], "7d": counts["7d"], "30d": counts["30d"], "failed": failed, "successful": success, "processed_links": links, "total_file_size": int(volume or 0), "by_site": [{"site": row[0], "count": int(row[1] or 0)} for row in result.all()]}

    async def charts(self, chart_range: str = "30d", metric: str = "users") -> dict[str, Any]:
        days = {"today": 1, "7d": 7, "30d": 30, "3mo": 90, "1yr": 365}.get(chart_range, 30)
        start = self.now - timedelta(days=days - 1)
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        if metric == "sales":
            model, where, value = Payment, Payment.status == PaymentStatus.APPROVED, func.count(Payment.id)
        elif metric == "subscriptions":
            model, where, value = Subscription, True, func.count(Subscription.id)
        elif metric == "downloads":
            model, where, value = DownloadJob, True, func.count(DownloadJob.id)
        else:
            model, where, value = User, User.status != UserStatus.DELETED, func.count(User.id)
        statement = select(func.date(model.created_at), value).where(model.created_at >= start, where).group_by(func.date(model.created_at)).order_by(func.date(model.created_at))
        result = await self.session.execute(statement)
        values = {str(row[0]): int(row[1] or 0) for row in result.all()}
        points = [{"date": (start.date() + timedelta(days=i)).isoformat(), "count": values.get((start.date() + timedelta(days=i)).isoformat(), 0)} for i in range(days)]
        return {"range": chart_range, "metric": metric, "points": points}

    async def kpis(self) -> dict[str, Any]:
        overview = await self.overview()
        users = await self.users()
        finance = await self.finance()
        subscriptions = await self.subscriptions()
        paying_users = finance["paying_users"]
        sold = subscriptions["total_sold"]
        churn = subscriptions["expired"] + subscriptions["cancelled"]
        return {"conversion_rate": round(paying_users / max(overview["total_users"], 1) * 100, 2), "retention": round(users["returning_users"] / max(overview["total_users"], 1) * 100, 2), "renewal_rate": round(subscriptions["renewed"] / max(sold, 1) * 100, 2), "churn_rate": round(churn / max(subscriptions["total_records"], 1) * 100, 2), "arpu_irt": round(finance["revenue"]["all"]["irt"] / max(overview["total_users"], 1), 2), "arppu_irt": round(finance["revenue"]["all"]["irt"] / max(paying_users, 1), 2), "ltv_irt": round(finance["revenue"]["all"]["irt"] / max(paying_users, 1), 2)}

    async def get(self, section: str, period: str = "30d", chart_range: str = "30d", metric: str = "users") -> dict[str, Any]:
        result: dict[str, Any] = {"section": section, "period": period}
        if section in {"overview", "all"}: result["overview"] = await self.overview()
        if section in {"users", "all"}: result["users"] = await self.users()
        if section in {"subscriptions", "all"}: result["subscriptions"] = await self.subscriptions()
        if section in {"finance", "all"}: result["finance"] = await self.finance()
        if section in {"downloads", "all"}: result["downloads"] = await self.downloads()
        if section in {"charts", "all"}: result["charts"] = await self.charts(chart_range, metric)
        if section in {"kpis", "all"}: result["kpis"] = await self.kpis()
        return result
