import os

import aiohttp
from aiogram.types import Message


BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "http://backend:8000",
).rstrip("/")


async def register_telegram_user(
    message: Message,
) -> dict:

    if not message.from_user:

        raise RuntimeError(
            "Telegram user information is missing."
        )

    user = (
        message.from_user
    )

    timeout = (
        aiohttp.ClientTimeout(
            total=15
        )
    )

    params = {
        "telegram_id":
            user.id,

        "username":
            user.username
            or "",

        "first_name":
            user.first_name
            or "",

        "last_name":
            user.last_name
            or "",

        "language_code":
            user.language_code
            or "",
    }

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            f"{BACKEND_URL}/users/telegram",
            params=params,
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )


# ============================================================
# Backend - media info
# ============================================================

async def get_media_info(
    source_url: str,
    playlist_index: int | None = None,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=180
        )
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            f"{BACKEND_URL}/downloads/info",
            params={
                "url":
                    source_url,

                **(
                    {
                        "playlist_index":
                            playlist_index,
                    }
                    if (
                        playlist_index
                        is not None
                    )
                    else {}
                ),
            },
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )


# ============================================================
# Backend - create
# ============================================================

async def create_download_job(
    source_url: str,
    quality: str | None = None,
    media_type: str = "video",
    playlist_index: int | None = None,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=30
        )
    )

    payload = {
        "source_url":
            source_url,

        "quality":
            quality,

        "media_type":
            media_type,

        "playlist_index":
            playlist_index,
    }

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            f"{BACKEND_URL}/downloads",
            json=payload,
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )

# ============================================================
# Backend - get
# ============================================================

async def get_download_job(
    job_id: int,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=15
        )
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            (
                f"{BACKEND_URL}"
                f"/downloads/{job_id}"
            ),
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )


# ============================================================
# Backend actions
# ============================================================

async def _post_job_action(
    job_id: int,
    action: str,
    fallback_error: str,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=15
        )
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            (
                f"{BACKEND_URL}"
                f"/downloads/"
                f"{job_id}/"
                f"{action}"
            ),
        ) as response:

            if (
                response.status
                >= 400
            ):

                try:

                    data = (
                        await response.json()
                    )

                    detail = (
                        data.get(
                            "detail",
                            fallback_error,
                        )
                    )

                except Exception:

                    detail = (
                        await response.text()
                        or fallback_error
                    )

                raise RuntimeError(
                    str(
                        detail
                    )
                )

            return (
                await response.json()
            )


async def pause_download_job(
    job_id: int,
) -> dict:

    return await _post_job_action(
        job_id,
        "pause",
        "Pause failed",
    )


async def resume_download_job(
    job_id: int,
) -> dict:

    return await _post_job_action(
        job_id,
        "resume",
        "Resume failed",
    )


async def cancel_download_job(
    job_id: int,
) -> dict:

    return await _post_job_action(
        job_id,
        "cancel",
        "Cancel failed",
    )
