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


async def get_telegram_user(telegram_id: int) -> dict:
    return await _payment_request("GET", f"/users/{telegram_id}")


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


async def update_application_setting(
    *,
    actor_telegram_id: int,
    key: str,
    category: str,
    value: object,
    expected_version: int,
    description: str | None,
) -> dict:
    return await _payment_request(
        "PUT",
        f"/admin/settings/{key}",
        payload={
            "actor_telegram_id": actor_telegram_id,
            "category": category,
            "value": value,
            "is_sensitive": False,
            "description": description,
            "expected_version": expected_version,
        },
    )


async def get_bot_configuration(language: str = "fa") -> dict:
    normalized = language if language in {"fa", "en"} else "fa"
    return await _payment_request(
        "GET",
        f"/bot/configuration?language={normalized}",
    )


async def list_home_buttons(actor_telegram_id: int) -> list[dict]:
    result = await _payment_request(
        "GET",
        f"/admin/home-buttons?actor_telegram_id={actor_telegram_id}",
    )
    return list(result)


async def create_home_button(*, actor_telegram_id: int, data: dict) -> dict:
    return await _payment_request(
        "POST",
        "/admin/home-buttons",
        payload={"actor_telegram_id": actor_telegram_id, **data},
    )


async def update_home_button(
    *,
    actor_telegram_id: int,
    button_id: int,
    changes: dict,
) -> dict:
    return await _payment_request(
        "PATCH",
        f"/admin/home-buttons/{button_id}",
        payload={"actor_telegram_id": actor_telegram_id, **changes},
    )


async def delete_home_button(*, actor_telegram_id: int, button_id: int) -> None:
    await _payment_request(
        "DELETE",
        f"/admin/home-buttons/{button_id}?actor_telegram_id={actor_telegram_id}",
    )


async def list_required_channels(actor_telegram_id: int) -> list[dict]:
    result = await _payment_request(
        "GET",
        f"/admin/required-channels?actor_telegram_id={actor_telegram_id}",
    )
    return list(result)


async def create_required_channel(*, actor_telegram_id: int, data: dict) -> dict:
    return await _payment_request(
        "POST",
        "/admin/required-channels",
        payload={"actor_telegram_id": actor_telegram_id, **data},
    )


async def update_required_channel(
    *,
    actor_telegram_id: int,
    channel_id: int,
    changes: dict,
) -> dict:
    return await _payment_request(
        "PATCH",
        f"/admin/required-channels/{channel_id}",
        payload={"actor_telegram_id": actor_telegram_id, **changes},
    )


async def delete_required_channel(
    *,
    actor_telegram_id: int,
    channel_id: int,
) -> None:
    await _payment_request(
        "DELETE",
        f"/admin/required-channels/{channel_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def create_support_ticket(
    *,
    telegram_id: int,
    category: str,
    body: str | None,
    telegram_file_id: str | None,
    file_type: str | None,
) -> dict:
    return await _payment_request(
        "POST",
        "/support/tickets",
        payload={
            "telegram_id": telegram_id,
            "category": category,
            "body": body,
            "telegram_file_id": telegram_file_id,
            "file_type": file_type,
        },
    )


async def list_support_tickets(
    actor_telegram_id: int,
    *,
    status: str | None = None,
) -> list[dict]:
    status_query = f"&status={status}" if status else ""
    result = await _payment_request(
        "GET",
        f"/admin/support/tickets?actor_telegram_id={actor_telegram_id}"
        f"{status_query}",
    )
    return list(result)


async def get_support_ticket(*, actor_telegram_id: int, ticket_id: int) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/support/tickets/{ticket_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def reply_support_ticket(
    *,
    actor_telegram_id: int,
    ticket_id: int,
    body: str,
) -> dict:
    return await _payment_request(
        "POST",
        f"/admin/support/tickets/{ticket_id}/reply",
        payload={"actor_telegram_id": actor_telegram_id, "body": body},
    )


async def close_support_ticket(*, actor_telegram_id: int, ticket_id: int) -> dict:
    return await _payment_request(
        "POST",
        f"/admin/support/tickets/{ticket_id}/close"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def get_download_entitlement(telegram_id: int) -> dict:
    return await _payment_request(
        "GET",
        f"/downloads/entitlement/{telegram_id}",
    )


async def get_admin_payment_summary(actor_telegram_id: int) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/payments/summary?actor_telegram_id={actor_telegram_id}",
    )


async def list_admin_payments(
    actor_telegram_id: int,
    *,
    status: str | None,
    page: int,
    page_size: int = 8,
) -> dict:
    status_query = f"&status={status}" if status else ""
    return await _payment_request(
        "GET",
        f"/admin/payments?actor_telegram_id={actor_telegram_id}"
        f"&page={page}&page_size={page_size}{status_query}",
    )


async def get_admin_payment(
    *,
    actor_telegram_id: int,
    payment_id: int,
) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/payments/{payment_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def list_payment_cards(actor_telegram_id: int) -> list[dict]:
    result = await _payment_request(
        "GET",
        f"/admin/payment-cards?actor_telegram_id={actor_telegram_id}",
    )
    return list(result)


async def get_payment_card(
    *,
    actor_telegram_id: int,
    card_id: int,
) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/payment-cards/{card_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def create_payment_card(
    *,
    actor_telegram_id: int,
    data: dict,
) -> dict:
    return await _payment_request(
        "POST",
        "/admin/payment-cards",
        payload={"actor_telegram_id": actor_telegram_id, **data},
    )


async def update_payment_card(
    *,
    actor_telegram_id: int,
    card_id: int,
    changes: dict,
) -> dict:
    return await _payment_request(
        "PATCH",
        f"/admin/payment-cards/{card_id}",
        payload={"actor_telegram_id": actor_telegram_id, **changes},
    )


async def delete_payment_card(
    *,
    actor_telegram_id: int,
    card_id: int,
) -> None:
    await _payment_request(
        "DELETE",
        f"/admin/payment-cards/{card_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def list_usdt_destinations(actor_telegram_id: int) -> list[dict]:
    result = await _payment_request(
        "GET",
        f"/admin/usdt-destinations?actor_telegram_id={actor_telegram_id}",
    )
    return list(result)


async def get_usdt_destination(
    *,
    actor_telegram_id: int,
    destination_id: int,
) -> dict:
    return await _payment_request(
        "GET",
        f"/admin/usdt-destinations/{destination_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


async def create_usdt_destination(
    *,
    actor_telegram_id: int,
    data: dict,
) -> dict:
    return await _payment_request(
        "POST",
        "/admin/usdt-destinations",
        payload={"actor_telegram_id": actor_telegram_id, **data},
    )


async def update_usdt_destination(
    *,
    actor_telegram_id: int,
    destination_id: int,
    changes: dict,
) -> dict:
    return await _payment_request(
        "PATCH",
        f"/admin/usdt-destinations/{destination_id}",
        payload={"actor_telegram_id": actor_telegram_id, **changes},
    )


async def delete_usdt_destination(
    *,
    actor_telegram_id: int,
    destination_id: int,
) -> None:
    await _payment_request(
        "DELETE",
        f"/admin/usdt-destinations/{destination_id}"
        f"?actor_telegram_id={actor_telegram_id}",
    )


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
            headers=_internal_headers(),
        ) as response:

            if response.status >= 400:
                try:
                    body = await response.json(content_type=None)
                    detail = body.get("detail", body)
                except Exception:
                    detail = await response.text() or "Media request failed"

                raise BackendAPIError(
                    status_code=response.status,
                    detail=detail,
                )

            return (
                await response.json()
            )


# ============================================================
# Backend - create
# ============================================================

async def create_download_job(
    source_url: str,
    telegram_id: int,
    quality: str | None = None,
    media_type: str = "video",
    playlist_index: int | None = None,
    estimated_size_bytes: int | None = None,
) -> dict:

    timeout = (
        aiohttp.ClientTimeout(
            total=30
        )
    )

    payload = {
        "telegram_id":
            telegram_id,

        "source_url":
            source_url,

        "quality":
            quality,

        "media_type":
            media_type,

        "playlist_index":
            playlist_index,

        "estimated_size_bytes":
            estimated_size_bytes,
    }

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            f"{BACKEND_URL}/downloads",
            json=payload,
            headers=_internal_headers(),
        ) as response:

            if response.status >= 400:
                try:
                    body = await response.json(content_type=None)
                    detail = body.get("detail", body)
                except Exception:
                    detail = await response.text() or "Download request failed"

                raise BackendAPIError(
                    status_code=response.status,
                    detail=detail,
                )

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
            headers=_internal_headers(),
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
            headers=_internal_headers(),
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

                raise BackendAPIError(
                    status_code=response.status,
                    detail=detail,
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


async def mark_download_delivered(
    job_id: int,
) -> dict:
    return await _post_job_action(
        job_id,
        "delivered",
        "Delivery confirmation failed",
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

            if response.status == 204:
                return {}

            return await response.json(content_type=None)


async def get_payment_configuration(
    *,
    select_destination: bool = True,
) -> dict:
    flag = "true" if select_destination else "false"
    return await _payment_request(
        "GET",
        f"/payments/configuration?select_destination={flag}",
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
    payment_card_id: int | None,
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
            "payment_card_id": payment_card_id,
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
