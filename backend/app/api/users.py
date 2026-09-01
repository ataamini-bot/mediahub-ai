from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.core.language import (
    effective_language,
    normalize_language,
)
from app.db.session import get_db
from app.models.user import User, UserStatus
from app.schemas.user import LanguageUpdate, TelegramUserResponse
from app.services.admin_access import AdminAccessService
from app.services.audit import AuditService


router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_internal_api_key)],
)


@router.post(
    "/telegram",
    response_model=TelegramUserResponse,
)
async def get_or_create_telegram_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(
            User.telegram_id == telegram_id
        )
    )

    user = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    telegram_language_code = (
        str(language_code or "").strip()[:10] or None
    )

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=telegram_language_code,
            status=UserStatus.ACTIVE,
            is_admin=False,
            last_activity_at=now,
        )

        db.add(user)

    else:
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.language_code = telegram_language_code
        user.last_activity_at = now

    await db.flush()
    await AdminAccessService(db).ensure_bootstrap_superadmin(user)

    await db.commit()
    await db.refresh(user)

    return TelegramUserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        preferred_language=user.preferred_language,
        effective_language=effective_language(
            preferred_language=user.preferred_language,
            telegram_language_code=user.language_code,
        ),
        status=user.status.value,
        is_admin=user.is_admin,
        last_activity_at=user.last_activity_at,
    )


@router.patch(
    "/{telegram_id}/language",
    response_model=TelegramUserResponse,
)
async def update_user_language(
    telegram_id: int,
    data: LanguageUpdate,
    db: AsyncSession = Depends(get_db),
) -> TelegramUserResponse:
    language = normalize_language(data.language)

    if language is None:
        raise HTTPException(
            status_code=422,
            detail="Supported languages are fa and en",
        )

    result = await db.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .with_for_update()
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="Telegram user not found")

    previous_language = effective_language(
        preferred_language=user.preferred_language,
        telegram_language_code=user.language_code,
    )
    user.preferred_language = language
    AuditService(db).record(
        action="user.language_changed",
        actor_user_id=user.id,
        actor_telegram_id=user.telegram_id,
        target_type="user",
        target_id=user.id,
        details={
            "previous_language": previous_language,
            "new_language": language,
        },
    )
    await db.commit()
    await db.refresh(user)

    return TelegramUserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        preferred_language=user.preferred_language,
        effective_language=language,
        status=user.status.value,
        is_admin=user.is_admin,
        last_activity_at=user.last_activity_at,
    )
