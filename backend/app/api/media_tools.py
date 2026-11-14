import asyncio
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.services.managed_settings import PublicOperationDisabled
from app.services.media_tool_config import read_config, public_config
from app.services.media_tool_files import remove_files
from app.services.media_tools import MediaToolError, owned, present, submit

router = APIRouter(prefix="/media-tools", tags=["media-tools"], dependencies=[Depends(require_internal_api_key)])


class ToolSubmit(BaseModel):
    telegram_id: int = Field(gt=0, le=2**63-1)
    request_id: UUID
    kind: Literal["music", "portrait"]
    clothing: Literal["natural", "suit", "diplomat", "shirt"] = "natural"
    background: Literal["white", "blue", "grey"] = "white"


class ToolOwner(BaseModel):
    telegram_id: int = Field(gt=0, le=2**63-1)


class SongDownload(ToolOwner):
    candidate: int = Field(ge=0, le=2)
    output_format: Literal["mp3", "m4a", "wav", "aac", "flac", "ogg", "opus"]


def failure(exc):
    return HTTPException(exc.status, {"code": exc.code})


@router.get("/configuration/{kind}")
async def configuration(kind: Literal["music", "portrait"], db: AsyncSession = Depends(get_db)):
    return public_config(await read_config(db, kind))


@router.post("")
async def start(data: ToolSubmit, db: AsyncSession = Depends(get_db)):
    try:
        row, created = await submit(db, data)
    except MediaToolError as exc:
        raise failure(exc) from exc
    except PublicOperationDisabled as exc:
        raise HTTPException(503, {"code": exc.code}) from exc
    if row.status == "pending":
        # Queue confirmation can be lost. Re-submitting the same identity is
        # safe: the Worker can claim it once only, including duplicate tasks.
        from app.services.media_tools import publish_tool
        try:
            await asyncio.to_thread(publish_tool, row.id)
            row.enqueued_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception:
            pass
    return present(row)


@router.get("/{request_id}")
async def status(request_id: UUID, telegram_id: int = Query(gt=0), db: AsyncSession = Depends(get_db)):
    try:
        return present(await owned(db, request_id, telegram_id))
    except MediaToolError as exc:
        raise failure(exc) from exc


@router.post("/{request_id}/cancel")
async def cancel(request_id: UUID, data: ToolOwner, db: AsyncSession = Depends(get_db)):
    try:
        row = await owned(db, request_id, data.telegram_id, lock=True)
    except MediaToolError as exc:
        raise failure(exc) from exc
    previous = row.status
    row.status = "cancelled"
    await db.commit()
    # An in-flight provider may already be charged. Let that Worker finish
    # and delete its files, without delivering a cancelled result.
    if previous != "processing":
        remove_files(data.telegram_id, request_id)
    return present(row)


@router.post("/{request_id}/release")
async def release(request_id: UUID, data: ToolOwner, db: AsyncSession = Depends(get_db)):
    try:
        row = await owned(db, request_id, data.telegram_id, lock=True)
    except MediaToolError as exc:
        raise failure(exc) from exc
    if row.status in {"pending", "processing"}:
        raise HTTPException(409, {"code": "tool_busy"})
    remove_files(data.telegram_id, request_id)
    row.files_removed_at = datetime.now(timezone.utc)
    row.delivery_state = "delivered"
    await db.commit()
    return {"released": True}


@router.post("/{request_id}/claim-delivery")
async def claim_delivery(request_id: UUID, data: ToolOwner, db: AsyncSession = Depends(get_db)):
    try:
        row = await owned(db, request_id, data.telegram_id, lock=True)
    except MediaToolError as exc:
        raise failure(exc) from exc
    if row.kind != "portrait" or row.status != "completed" or row.files_removed_at or row.delivery_state:
        return {"claimed": False}
    row.delivery_state = "sending"
    await db.commit()
    return {"claimed": True}


@router.post("/{request_id}/download")
async def download_song(request_id: UUID, data: SongDownload, db: AsyncSession = Depends(get_db)):
    from app.models.download_job import DownloadJob
    from app.schemas.download import DownloadResponse
    from app.services.download import DownloadService, DownloadFormatError
    from app.services.download_access import DownloadAccessError
    try:
        row = await owned(db, request_id, data.telegram_id, lock=True)
    except MediaToolError as exc:
        raise failure(exc) from exc
    if row.kind != "music" or row.status != "completed":
        raise HTTPException(409, {"code": "music_not_ready"})
    selection_key = f"{data.candidate}:{data.output_format}"
    selection = (row.downloads or {}).get(selection_key) or {}
    if selection.get("job_id"):
        job = await db.get(DownloadJob, selection["job_id"])
        if not job:
            raise HTTPException(409, {"code": "not_found"})
        return DownloadResponse.model_validate(job)
    if selection.get("state") == "starting":
        raise HTTPException(409, {"code": "download_start_uncertain"})
    candidates = (row.result or {}).get("candidates") or []
    if data.candidate >= len(candidates):
        raise HTTPException(422, {"code": "invalid_candidate"})
    url = candidates[data.candidate]["url"]
    row.download_state = "starting"
    row.downloads = {**(row.downloads or {}), selection_key: {"state": "starting"}}
    await db.commit()
    try:
        # Only a source from the server's full-song candidates can reach the
        # ordinary audio downloader. An uploaded clip can never reach here.
        job = await DownloadService(db).create_job(source_url=url, telegram_id=data.telegram_id,
                    media_type="audio", output_format=data.output_format)
    except (DownloadAccessError, DownloadFormatError) as exc:
        await db.rollback()
        row = await owned(db, request_id, data.telegram_id, lock=True)
        row.download_state = "rejected"
        row.downloads = {**(row.downloads or {}), selection_key: {"state": "rejected"}}
        await db.commit()
        if isinstance(exc, DownloadAccessError):
            raise HTTPException(exc.status_code, exc.detail()) from exc
        raise HTTPException(422, {"code": "unsupported_format"}) from exc
    # An unknown crash between create_job and this commit is deliberately not
    # retried. It cannot create another charged/quota-consuming download.
    row = await owned(db, request_id, data.telegram_id, lock=True)
    row.download_job_id, row.download_state = job.id, "created"
    row.downloads = {**(row.downloads or {}), selection_key: {"state": "created", "job_id": job.id}}
    await db.commit()
    return DownloadResponse.model_validate(job)
