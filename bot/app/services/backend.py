import os

import aiohttp
from aiogram.types import Message


BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "http://backend:8000",
).rstrip("/")

BOT_BACKEND_API_KEY = os.getenv(
    "BOT_BACKEND_API_KEY",
    "",
).strip()


class BackendAPIError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        detail: object,
    ):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def _internal_headers() -> dict[str, str]:
    if not BOT_BACKEND_API_KEY:
        return {}

    return {
        "X-Internal-API-Key": BOT_BACKEND_API_KEY,
    }


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
            headers=_internal_headers(),
        ) as response:

            response.raise_for_status()

            return (
                await response.json()
            )


async def set_user_language(
    telegram_id: int,
    language: str,
) -> dict:
    return await _payment_request(
        "PATCH",
        f"/users/{telegram_id}/language",
        payload={"language": language},
    )


async def get_admin_context(telegram_id: int) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/context/{telegram_id}",
    )


async def list_application_settings(
    actor_telegram_id: int,
) -> list[dict]:
    result = await _payment_request(
        "GET",
        f"/admin/settings?actor_telegram_id={actor_telegram_id}",
    )
    return list(result)


async def list_admin_plans(
    actor_telegram_id: int,
    *,
    include_inactive: bool = True,
) -> list[dict]:
    flag = "true" if include_inactive else "false"
    result = await _payment_request(
        "GET",
        f"/admin/plans?actor_telegram_id={actor_telegram_id}"
        f"&include_inactive={flag}",
    )
    return list(result)


async def get_admin_plan(
    *,
    actor_telegram_id: int,
    plan_id: int,
) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/plans/{plan_id}?actor_telegram_id={actor_telegram_id}",
    )


async def create_admin_plan(
    *,
    actor_telegram_id: int,
    plan: dict,
    reason: str,
) -> dict:
    return await _payment_request(
        "POST",
        "/admin/plans",
        payload={
            "actor_telegram_id": actor_telegram_id,
            **plan,
            "reason": reason,
        },
    )


async def update_admin_plan(
    *,
    actor_telegram_id: int,
    plan_id: int,
    changes: dict,
    reason: str,
) -> dict:
    return await _payment_request(
        "PATCH",
        f"/admin/plans/{plan_id}",
        payload={
            "actor_telegram_id": actor_telegram_id,
            **changes,
            "reason": reason,
        },
    )


async def list_admin_accounts(
    actor_telegram_id: int,
) -> list[dict]:
    result = await _payment_request(
        "GET",
        f"/admin/accounts?actor_telegram_id={actor_telegram_id}",
    )
    return list(result)


async def get_admin_account(
    *,
    actor_telegram_id: int,
    target_telegram_id: int,
) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/accounts/{target_telegram_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def create_admin_account(
    *,
    actor_telegram_id: int,
    target_telegram_id: int,
    role_codes: list[str],
    is_superadmin: bool,
    reason: str,
) -> dict:
    return await _payment_request(
        "POST",
        "/admin/accounts",
        payload={
            "actor_telegram_id": actor_telegram_id,
            "target_telegram_id": target_telegram_id,
            "role_codes": role_codes,
            "is_superadmin": is_superadmin,
            "reason": reason,
        },
    )


async def update_admin_account(
    *,
    actor_telegram_id: int,
    target_telegram_id: int,
    reason: str,
    role_codes: list[str] | None = None,
    is_superadmin: bool | None = None,
    is_active: bool | None = None,
) -> dict:
    payload: dict = {
        "actor_telegram_id": actor_telegram_id,
        "reason": reason,
    }

    if role_codes is not None:
        payload["role_codes"] = role_codes

    if is_superadmin is not None:
        payload["is_superadmin"] = is_superadmin

    if is_active is not None:
        payload["is_active"] = is_active

    return await _payment_request(
        "PATCH",
        f"/admin/accounts/{target_telegram_id}",
        payload=payload,
    )


async def list_admin_roles(
    actor_telegram_id: int,
    *,
    include_inactive: bool = True,
) -> list[dict]:
    flag = "true" if include_inactive else "false"
    result = await _payment_request(
        "GET",
        f"/admin/roles?actor_telegram_id={actor_telegram_id}"
        f"&include_inactive={flag}",
    )
    return list(result)


async def list_admin_permissions(
    actor_telegram_id: int,
) -> list[dict]:
    result = await _payment_request(
        "GET",
        f"/admin/permissions?actor_telegram_id={actor_telegram_id}",
    )
    return list(result)


async def create_admin_role(
    *,
    actor_telegram_id: int,
    code: str,
    name: str,
    description: str | None,
    permission_codes: list[str],
    reason: str,
) -> dict:
    return await _payment_request(
        "POST",
        "/admin/roles",
        payload={
            "actor_telegram_id": actor_telegram_id,
            "code": code,
            "name": name,
            "description": description,
            "permission_codes": permission_codes,
            "reason": reason,
        },
    )


async def update_admin_role(
    *,
    actor_telegram_id: int,
    role_id: int,
    reason: str,
    name: str | None = None,
    description: str | None = None,
    description_supplied: bool = False,
    permission_codes: list[str] | None = None,
    is_active: bool | None = None,
) -> dict:
    payload: dict = {
        "actor_telegram_id": actor_telegram_id,
        "reason": reason,
    }

    if name is not None:
        payload["name"] = name

    if description_supplied:
        payload["description"] = description

    if permission_codes is not None:
        payload["permission_codes"] = permission_codes

    if is_active is not None:
        payload["is_active"] = is_active

    return await _payment_request(
        "PATCH",
        f"/admin/roles/{role_id}",
        payload=payload,
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


# ============================================================
# Backend - manual payments
# ============================================================

async def _payment_request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
) -> dict:
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            f"{BACKEND_URL}{path}",
            json=payload,
            headers=_internal_headers(),
        ) as response:
            if response.status >= 400:
                try:
                    body = await response.json(content_type=None)
                    detail = body.get("detail", body)
                except Exception:
                    detail = await response.text() or "Backend request failed"

                raise BackendAPIError(
                    status_code=response.status,
                    detail=detail,
                )

            return await response.json(content_type=None)


async def get_payment_configuration() -> dict:
    return await _payment_request(
        "GET",
        "/payments/configuration",
    )


async def create_manual_payment(
    *,
    telegram_id: int,
    offer_code: str,
    receipt_file_id: str,
    receipt_file_unique_id: str | None,
    receipt_file_type: str,
    receipt_file_size: int | None,
    receipt_mime_type: str | None,
    receipt_file_name: str | None,
    user_receipt_message_id: int,
) -> dict:
    return await _payment_request(
        "POST",
        "/payments",
        payload={
            "telegram_id": telegram_id,
            "offer_code": offer_code,
            "receipt_file_id": receipt_file_id,
            "receipt_file_unique_id": receipt_file_unique_id,
            "receipt_file_type": receipt_file_type,
            "receipt_file_size": receipt_file_size,
            "receipt_mime_type": receipt_mime_type,
            "receipt_file_name": receipt_file_name,
            "user_receipt_message_id": user_receipt_message_id,
        },
    )


async def set_payment_admin_message(
    *,
    payment_id: int,
    admin_chat_id: int,
    admin_message_id: int,
    admin_message_thread_id: int | None,
) -> dict:
    return await _payment_request(
        "PATCH",
        f"/payments/{payment_id}/admin-message",
        payload={
            "admin_chat_id": admin_chat_id,
            "admin_message_id": admin_message_id,
            "admin_message_thread_id": admin_message_thread_id,
        },
    )


async def mark_payment_delivery_failed(payment_id: int) -> dict:
    return await _payment_request(
        "POST",
        f"/payments/{payment_id}/delivery-failed",
    )


async def approve_manual_payment(
    *,
    payment_id: int,
    admin_telegram_id: int,
) -> dict:
    return await _payment_request(
        "POST",
        f"/payments/{payment_id}/approve",
        payload={"admin_telegram_id": admin_telegram_id},
    )


async def reject_manual_payment(
    *,
    payment_id: int,
    admin_telegram_id: int,
    reason: str,
) -> dict:
    return await _payment_request(
        "POST",
        f"/payments/{payment_id}/reject",
        payload={
            "admin_telegram_id": admin_telegram_id,
            "reason": reason,
        },
    )


async def get_current_subscription(telegram_id: int) -> dict:
    return await _payment_request(
        "GET",
        f"/payments/subscription/{telegram_id}",
    )
