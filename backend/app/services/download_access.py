import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.admin import AdminAccount
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.models.plan import Plan
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserStatus
from app.services.managed_settings import (
    PublicOperationDisabled,
    ensure_public_operation,
    get_managed_setting,
)


TECHNICAL_MAX_FILE_SIZE_MB = 1900
ADMIN_MAX_CONCURRENT_DOWNLOADS = 3


def quota_day_start_utc(
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> datetime:
    """Return the current quota day's local midnight in UTC."""
    current_utc = now or datetime.now(timezone.utc)
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)

    quota_zone = ZoneInfo(timezone_name or settings.quota_timezone)
    return (
        current_utc.astimezone(quota_zone)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )


class DownloadAccessError(RuntimeError):
    code = "download_access_error"
    status_code = 400

    def __init__(self, message: str, **context: object):
        super().__init__(message)
        self.context = context

    def detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            **self.context,
        }


class DownloadUserNotFound(DownloadAccessError):
    code = "download_user_not_found"
    status_code = 404


class DownloadUserBlocked(DownloadAccessError):
    code = "download_user_blocked"
    status_code = 403


class DownloadPlanUnavailable(DownloadAccessError):
    code = "download_plan_unavailable"
    status_code = 503


class DailyDownloadLimitReached(DownloadAccessError):
    code = "daily_download_limit_reached"
    status_code = 429


class ConcurrentDownloadLimitReached(DownloadAccessError):
    code = "concurrent_download_limit_reached"
    status_code = 429


class DownloadQualityLimitExceeded(DownloadAccessError):
    code = "download_quality_limit_exceeded"
    status_code = 422


class DownloadFileSizeLimitExceeded(DownloadAccessError):
    code = "download_file_size_limit_exceeded"
    status_code = 413


class DownloadTemporarilyUnavailable(DownloadAccessError):
    code = "download_temporarily_unavailable"
    status_code = 503


@dataclass(frozen=True, slots=True)
class DownloadEntitlement:
    user_id: int
    plan_id: int | None
    plan_name: str
    daily_download_limit: int | None
    max_file_size_mb: int
    max_quality: int | None
    max_concurrent_downloads: int
    priority_processing: bool
    forced_join_required: bool
    is_admin_bypass: bool = False

    def limits_snapshot(self) -> dict[str, object]:
        return {
            "daily_download_limit": self.daily_download_limit,
            "max_file_size_mb": self.max_file_size_mb,
            "max_quality": self.max_quality,
            "max_concurrent_downloads": self.max_concurrent_downloads,
            "priority_processing": self.priority_processing,
            "forced_join_required": self.forced_join_required,
            "is_admin_bypass": self.is_admin_bypass,
        }


class DownloadAccessService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def parse_quality_height(value: str | None) -> int | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if normalized == "4k":
            return 2160
        if normalized == "2k":
            return 1440

        match = re.search(r"(\d{3,4})", normalized)
        return int(match.group(1)) if match else None

    async def authorize_job(
        self,
        *,
        telegram_id: int,
        quality: str | None,
        estimated_size_bytes: int | None,
    ) -> DownloadEntitlement:
        try:
            await ensure_public_operation(self.session, "downloads")
        except PublicOperationDisabled as exc:
            raise DownloadTemporarilyUnavailable(
                str(exc),
                reason_code=exc.code,
            ) from exc

        user_result = await self.session.execute(
            select(User)
            .where(User.telegram_id == telegram_id)
            .with_for_update()
        )
        user = user_result.scalar_one_or_none()

        if user is None:
            raise DownloadUserNotFound(
                "Telegram user must be registered before downloading"
            )
        if user.status != UserStatus.ACTIVE:
            raise DownloadUserBlocked("User account is not active")

        entitlement = await self._resolve_entitlement(user)
        self._validate_quality(entitlement, quality)
        self._validate_estimated_size(entitlement, estimated_size_bytes)

        if not entitlement.is_admin_bypass:
            await self._validate_daily_limit(entitlement)
            await self._validate_concurrency(entitlement)

        return entitlement

    async def mark_delivered(self, job_id: int) -> DownloadJob:
        result = await self.session.execute(
            select(DownloadJob)
            .where(DownloadJob.id == job_id)
            .with_for_update()
        )
        job = result.scalar_one_or_none()

        if job is None:
            raise LookupError("Download job not found")
        if job.status != DownloadJobStatus.COMPLETED:
            raise ValueError("Only a completed download can be delivered")

        if job.delivered_at is None:
            job.delivered_at = datetime.now(timezone.utc)
            await self.session.commit()
            await self.session.refresh(job)

        return job

    async def authorize_resume(self, job: DownloadJob) -> bool:
        snapshot = (
            job.plan_limits_snapshot
            if isinstance(job.plan_limits_snapshot, dict)
            else {}
        )
        priority_processing = bool(snapshot.get("priority_processing"))

        if job.user_id is None or bool(snapshot.get("is_admin_bypass")):
            return priority_processing

        await self.session.execute(
            select(User.id)
            .where(User.id == job.user_id)
            .with_for_update()
        )
        try:
            concurrency_limit = int(
                snapshot.get("max_concurrent_downloads") or 1
            )
        except (TypeError, ValueError):
            concurrency_limit = 1

        result = await self.session.execute(
            select(func.count(DownloadJob.id)).where(
                DownloadJob.user_id == job.user_id,
                DownloadJob.id != job.id,
                DownloadJob.status.in_(
                    (
                        DownloadJobStatus.PENDING,
                        DownloadJobStatus.PROCESSING,
                    )
                ),
            )
        )
        active_downloads = int(result.scalar_one())

        if active_downloads >= concurrency_limit:
            raise ConcurrentDownloadLimitReached(
                "Concurrent download limit has been reached",
                plan_name=job.plan_name_snapshot or "Current plan",
                limit=concurrency_limit,
                active=active_downloads,
            )

        return priority_processing

    async def _resolve_entitlement(self, user: User) -> DownloadEntitlement:
        admin_result = await self.session.execute(
            select(AdminAccount.id).where(
                AdminAccount.user_id == user.id,
                AdminAccount.is_active.is_(True),
            )
        )
        if admin_result.scalar_one_or_none() is not None:
            return DownloadEntitlement(
                user_id=user.id,
                plan_id=None,
                plan_name="Administrator",
                daily_download_limit=None,
                max_file_size_mb=TECHNICAL_MAX_FILE_SIZE_MB,
                max_quality=None,
                max_concurrent_downloads=ADMIN_MAX_CONCURRENT_DOWNLOADS,
                priority_processing=True,
                forced_join_required=False,
                is_admin_bypass=True,
            )

        now = datetime.now(timezone.utc)
        plan_result = await self.session.execute(
            select(Plan)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(
                Subscription.user_id == user.id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.started_at <= now,
                Subscription.expires_at > now,
            )
            .order_by(
                Subscription.expires_at.desc(),
                Subscription.id.desc(),
            )
            .limit(1)
        )
        plan = plan_result.scalar_one_or_none()

        if plan is None:
            free_result = await self.session.execute(
                select(Plan).where(
                    Plan.slug == "free",
                    Plan.is_active.is_(True),
                    Plan.deleted_at.is_(None),
                )
            )
            plan = free_result.scalar_one_or_none()

        if plan is None or plan.max_file_size_mb is None:
            raise DownloadPlanUnavailable(
                "No usable download plan is configured"
            )

        return DownloadEntitlement(
            user_id=user.id,
            plan_id=plan.id,
            plan_name=plan.name,
            daily_download_limit=plan.daily_download_limit,
            max_file_size_mb=min(
                plan.max_file_size_mb,
                TECHNICAL_MAX_FILE_SIZE_MB,
            ),
            max_quality=plan.max_quality,
            max_concurrent_downloads=plan.max_concurrent_downloads,
            priority_processing=plan.priority_processing,
            forced_join_required=plan.forced_join_required,
        )

    @staticmethod
    def _validate_quality(
        entitlement: DownloadEntitlement,
        quality: str | None,
    ) -> None:
        requested_quality = DownloadAccessService.parse_quality_height(quality)
        maximum_quality = entitlement.max_quality

        if (
            requested_quality is not None
            and maximum_quality is not None
            and requested_quality > maximum_quality
        ):
            raise DownloadQualityLimitExceeded(
                "Requested quality exceeds the plan limit",
                plan_name=entitlement.plan_name,
                requested_quality=requested_quality,
                max_quality=maximum_quality,
            )

    @staticmethod
    def _validate_estimated_size(
        entitlement: DownloadEntitlement,
        estimated_size_bytes: int | None,
    ) -> None:
        if estimated_size_bytes is None:
            return

        maximum_bytes = entitlement.max_file_size_mb * 1024 * 1024
        if estimated_size_bytes > maximum_bytes:
            raise DownloadFileSizeLimitExceeded(
                "Estimated file size exceeds the plan limit",
                plan_name=entitlement.plan_name,
                estimated_size_bytes=estimated_size_bytes,
                max_file_size_mb=entitlement.max_file_size_mb,
            )

    async def _validate_daily_limit(
        self,
        entitlement: DownloadEntitlement,
    ) -> None:
        limit = entitlement.daily_download_limit
        if limit is None:
            return

        timezone_name = await get_managed_setting(
            self.session,
            "quota.timezone",
        )
        day_start = quota_day_start_utc(timezone_name=timezone_name)
        reserved_statuses = (
            DownloadJobStatus.PENDING,
            DownloadJobStatus.PROCESSING,
            DownloadJobStatus.PAUSED,
        )
        result = await self.session.execute(
            select(func.count(DownloadJob.id)).where(
                DownloadJob.user_id == entitlement.user_id,
                or_(
                    DownloadJob.delivered_at >= day_start,
                    DownloadJob.status.in_(reserved_statuses),
                    and_(
                        DownloadJob.status == DownloadJobStatus.COMPLETED,
                        DownloadJob.delivered_at.is_(None),
                        DownloadJob.created_at >= day_start,
                    ),
                ),
            )
        )
        used_or_reserved = int(result.scalar_one())

        if used_or_reserved >= limit:
            raise DailyDownloadLimitReached(
                "Daily successful output limit has been reached",
                plan_name=entitlement.plan_name,
                limit=limit,
                used=used_or_reserved,
            )

    async def _validate_concurrency(
        self,
        entitlement: DownloadEntitlement,
    ) -> None:
        result = await self.session.execute(
            select(func.count(DownloadJob.id)).where(
                DownloadJob.user_id == entitlement.user_id,
                DownloadJob.status.in_(
                    (
                        DownloadJobStatus.PENDING,
                        DownloadJobStatus.PROCESSING,
                    )
                ),
            )
        )
        active_downloads = int(result.scalar_one())

        if active_downloads >= entitlement.max_concurrent_downloads:
            raise ConcurrentDownloadLimitReached(
                "Concurrent download limit has been reached",
                plan_name=entitlement.plan_name,
                limit=entitlement.max_concurrent_downloads,
                active=active_downloads,
            )
