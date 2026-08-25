from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User, UserStatus


router = APIRouter(
    prefix="/users",
    tags=["users"],
)


@router.post(
    "/telegram",
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

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            status=UserStatus.ACTIVE,
            is_admin=False,
            last_activity_at=now,
        )

        db.add(user)

    else:
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.language_code = language_code
        user.last_activity_at = now

    await db.commit()
    await db.refresh(user)

    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "language_code": user.language_code,
        "status": user.status,
        "is_admin": user.is_admin,
        "last_activity_at": user.last_activity_at,
    }
