from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.download_job import (
    DownloadJob,
    DownloadJobStatus,
)
from app.models.plan import Plan
from app.models.subscription import (
    Subscription,
    SubscriptionStatus,
)
from app.models.user import User


QUOTA_LOCK_NAMESPACE = 1296581713

COUNTED_DOWNLOAD_STATUSES = (
    DownloadJobStatus.PENDING,
    DownloadJobStatus.PROCESSING,
    DownloadJobStatus.PAUSED,
    DownloadJobStatus.COMPLETED,
)


class DailyDownloadLimitExceeded(RuntimeError):

    def __init__(
        self,
        *,
        limit: int,
        used: int,
        reset_at: datetime,
        plan_slug: str,
    ):
        self.limit = limit
        self.used = used
        self.reset_at = reset_at
        self.plan_slug = plan_slug

        super().__init__(
            "Daily download limit reached"
        )


class DownloadQuotaService:

    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_effective_plan(
        self,
        *,
        user_id: int,
        now: datetime,
    ) -> Plan:
        result = await self.session.execute(
            select(Plan)
            .join(
                Subscription,
                Subscription.plan_id == Plan.id,
            )
            .where(
                Subscription.user_id == user_id,
                Subscription.status
                == SubscriptionStatus.ACTIVE,
                Subscription.started_at <= now,
                Subscription.expires_at > now,
                Plan.is_active.is_(True),
            )
            .order_by(
                Subscription.expires_at.desc(),
                Subscription.id.desc(),
            )
            .limit(1)
        )

        plan = result.scalars().first()

        if plan is not None:
            return plan

        result = await self.session.execute(
            select(Plan).where(
                Plan.slug == "free",
                Plan.is_active.is_(True),
            )
        )

        plan = result.scalar_one_or_none()

        if plan is None:
            raise RuntimeError(
                "Active free plan is not configured"
            )

        return plan

    async def reserve_daily_slot(
        self,
        *,
        user_id: int,
    ) -> None:
        result = await self.session.execute(
            select(User.is_admin).where(
                User.id == user_id,
            )
        )

        if result.scalar_one_or_none() is True:
            return

        # The lock remains active until the DownloadJob insert
        # is committed or the transaction is rolled back.
        await self.session.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    CAST(:namespace AS integer),
                    CAST(:user_id AS integer)
                )
                """
            ),
            {
                "namespace": QUOTA_LOCK_NAMESPACE,
                "user_id": user_id,
            },
        )

        now = datetime.now(timezone.utc)

        plan = await self.get_effective_plan(
            user_id=user_id,
            now=now,
        )

        limit = plan.daily_download_limit

        if limit is None:
            return

        quota_timezone = ZoneInfo(
            settings.quota_timezone
        )

        local_now = now.astimezone(
            quota_timezone
        )

        day_start_local = local_now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        day_end_local = (
            day_start_local
            + timedelta(days=1)
        )

        day_start_utc = (
            day_start_local.astimezone(
                timezone.utc
            )
        )
        day_end_utc = (
            day_end_local.astimezone(
                timezone.utc
            )
        )

        result = await self.session.execute(
            select(
                func.count(DownloadJob.id)
            ).where(
                DownloadJob.user_id == user_id,
                DownloadJob.created_at
                >= day_start_utc,
                DownloadJob.created_at
                < day_end_utc,
                DownloadJob.status.in_(
                    COUNTED_DOWNLOAD_STATUSES
                ),
            )
        )

        used = int(
            result.scalar_one()
            or 0
        )

        if used >= limit:
            raise DailyDownloadLimitExceeded(
                limit=limit,
                used=used,
                reset_at=day_end_utc,
                plan_slug=plan.slug,
            )
