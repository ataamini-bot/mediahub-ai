import secrets
from typing import Annotated

from fastapi import Header, HTTPException

from app.core.config import settings


async def require_internal_api_key(
    x_internal_api_key: Annotated[str | None, Header()] = None,
) -> None:
    configured_key = settings.bot_backend_api_key.strip()

    if (
        len(configured_key) < 32
        or configured_key == "CHANGE_ME"
    ):
        raise HTTPException(
            status_code=503,
            detail="Internal bot API key is not configured",
        )

    provided_key = (x_internal_api_key or "").strip()

    if not secrets.compare_digest(configured_key, provided_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid internal API key",
        )
