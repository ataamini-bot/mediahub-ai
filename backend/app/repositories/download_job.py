from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.download_job import (
    DownloadJob,
    DownloadJobStatus,
)


class DownloadJobRepository:

    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def create(
        self,
        source_url: str,
        user_id: int | None = None,
        plan_id: int | None = None,
        plan_name_snapshot: str | None = None,
        plan_limits_snapshot: dict | None = None,
        format_id: str | None = None,
        quality: str | None = None,
        media_type: str | None = None,
        playlist_index: int | None = None,
    ) -> DownloadJob:

        job = DownloadJob(
            source_url=source_url,
            user_id=user_id,
            plan_id=plan_id,
            plan_name_snapshot=plan_name_snapshot,
            plan_limits_snapshot=plan_limits_snapshot,
            format_id=format_id,
            quality=quality,
            media_type=media_type,
            playlist_index=playlist_index,
            status=DownloadJobStatus.PENDING,
        )

        self.session.add(job)

        await self.session.commit()
        await self.session.refresh(job)

        return job

    async def get_by_id(
        self,
        job_id: int,
    ) -> DownloadJob | None:

        return await self.session.get(
            DownloadJob,
            job_id,
        )

    async def set_celery_task_id(
        self,
        job: DownloadJob,
        celery_task_id: str,
    ) -> DownloadJob:

        job.celery_task_id = celery_task_id

        await self.session.commit()
        await self.session.refresh(job)

        return job

    async def mark_paused(
        self,
        job: DownloadJob,
    ) -> DownloadJob:

        job.status = DownloadJobStatus.PAUSED
        job.paused_at = datetime.now(timezone.utc)
        job.cancelled_at = None
        job.expired_at = None

        await self.session.commit()
        await self.session.refresh(job)

        return job

    async def mark_resumed(
        self,
        job: DownloadJob,
        celery_task_id: str,
    ) -> DownloadJob:

        job.status = DownloadJobStatus.PENDING
        job.celery_task_id = celery_task_id
        job.paused_at = None
        job.cancelled_at = None
        job.expired_at = None
        job.error_message = None

        await self.session.commit()
        await self.session.refresh(job)

        return job

    async def mark_cancelled(
        self,
        job: DownloadJob,
    ) -> DownloadJob:

        job.status = DownloadJobStatus.CANCELLED
        job.cancelled_at = datetime.now(timezone.utc)
        job.paused_at = None

        await self.session.commit()
        await self.session.refresh(job)

        return job
