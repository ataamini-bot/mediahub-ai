import asyncio

import mimetypes
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from fastapi.responses import (
    FileResponse,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.db.session import (
    get_db,
)
from app.schemas.download import (
    DownloadCreate,
    DownloadResponse,
    MediaInfoResponse,
)
from app.services.instagram_auth_monitor import (
    notify_instagram_story_auth_issue,
    notify_instagram_story_auth_recovery,
)

from app.services.download import (
    DownloadService,
)
from app.models.download_job import (
    DownloadJobStatus,
)


router = APIRouter(
    prefix="/downloads",
    tags=[
        "downloads",
    ],
)


# ============================================================
# Create download
# ============================================================

@router.post(
    "",
    response_model=DownloadResponse,
)
async def create_download(
    data: DownloadCreate,
    db: AsyncSession = Depends(
        get_db
    ),
):
    service = DownloadService(
        db
    )

    job = await service.create_job(
        source_url=data.source_url,
        user_id=None,
        format_id=data.format_id,
        quality=data.quality,
        media_type=data.media_type,
        playlist_index=(
            data.playlist_index
        ),
    )

    return job


# ============================================================
# Media info
# ============================================================

@router.get(
    "/info",
    response_model=MediaInfoResponse,
)
async def get_media_info(
    url: str = Query(
        ...,
        min_length=5,
        max_length=2048,
    ),
    playlist_index: int | None = Query(
        default=None,
        ge=1,
    ),
):
    try:

        media_info = await asyncio.to_thread(
            DownloadService.get_media_info,
            source_url=url,
            playlist_index=playlist_index,
        )

        notify_instagram_story_auth_recovery(
            source_url=url,
            service="Backend",
        )

        return media_info

    except Exception as exc:

        notify_instagram_story_auth_issue(
            source_url=url,
            error=exc,
            service="Backend",
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "Failed to extract media information: "
                f"{str(exc)[:1000]}"
            ),
        )


# ============================================================
# Get download
# ============================================================

@router.get(
    "/{job_id}",
    response_model=DownloadResponse,
)
async def get_download(
    job_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
):
    service = DownloadService(
        db
    )

    job = await service.get_job(
        job_id
    )

    if job is None:

        raise HTTPException(
            status_code=404,
            detail=(
                "Download job not found"
            ),
        )

    return job


# ============================================================
# Pause download
# ============================================================

@router.post(
    "/{job_id}/pause",
    response_model=DownloadResponse,
)
async def pause_download(
    job_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
):
    service = DownloadService(
        db
    )

    try:

        return (
            await service.pause_job(
                job_id
            )
        )

    except LookupError as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=409,
            detail=str(
                exc
            ),
        )


# ============================================================
# Resume download
# ============================================================

@router.post(
    "/{job_id}/resume",
    response_model=DownloadResponse,
)
async def resume_download(
    job_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
):
    service = DownloadService(
        db
    )

    try:

        return (
            await service.resume_job(
                job_id
            )
        )

    except LookupError as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=409,
            detail=str(
                exc
            ),
        )


# ============================================================
# Cancel download
# ============================================================

@router.post(
    "/{job_id}/cancel",
    response_model=DownloadResponse,
)
async def cancel_download(
    job_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
):
    service = DownloadService(
        db
    )

    try:

        return (
            await service.cancel_job(
                job_id
            )
        )

    except LookupError as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=409,
            detail=str(
                exc
            ),
        )


# ============================================================
# Download file
# ============================================================

@router.get(
    "/{job_id}/file",
)
async def download_file(
    job_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
):
    service = DownloadService(
        db
    )

    job = await service.get_job(
        job_id
    )

    if job is None:

        raise HTTPException(
            status_code=404,
            detail=(
                "Download job not found"
            ),
        )

    if (
        job.status
        != DownloadJobStatus.COMPLETED
    ):

        raise HTTPException(
            status_code=409,
            detail=(
                "Download is not completed yet"
            ),
        )

    if not job.file_path:

        raise HTTPException(
            status_code=404,
            detail=(
                "Downloaded file not found"
            ),
        )

    file_path = Path(
        job.file_path
    )

    suffix = (
        file_path.suffix
        .lower()
    )

    guessed_media_type = (
        mimetypes.guess_type(
            file_path.name
        )[0]
        or "application/octet-stream"
    )

    return FileResponse(
        path=job.file_path,
        filename=(
            f"download-{job.id}"
            f"{suffix}"
        ),
        media_type=(
            guessed_media_type
        ),
    )
