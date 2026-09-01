from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.models.app_setting import ApplicationSetting
from app.schemas.admin import (
    AdminContextResponse,
    ApplicationSettingResponse,
    ApplicationSettingUpdate,
)
from app.services.admin_access import (
    AdminAccessDenied,
    AdminAccessService,
    PermissionCode,
)
from app.services.application_settings import (
    ApplicationSettingsService,
    SettingConflict,
    SettingEncryptionError,
    SettingValidationError,
)


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_internal_api_key)],
)


def serialize_setting(setting: ApplicationSetting) -> ApplicationSettingResponse:
    is_configured = (
        bool(setting.encrypted_value)
        if setting.is_sensitive
        else setting.value_json is not None
    )
    return ApplicationSettingResponse(
        key=setting.key,
        category=setting.category,
        value=None if setting.is_sensitive else setting.value_json,
        is_sensitive=setting.is_sensitive,
        is_configured=is_configured,
        description=setting.description,
        version=setting.version,
    )


@router.get(
    "/context/{telegram_id}",
    response_model=AdminContextResponse,
)
async def admin_context(
    telegram_id: int,
    db: AsyncSession = Depends(get_db),
) -> AdminContextResponse:
    context = await AdminAccessService(db).get_context(telegram_id)
    return AdminContextResponse(
        telegram_id=context.telegram_id,
        user_id=context.user_id,
        admin_account_id=context.admin_account_id,
        is_admin=context.is_admin,
        is_superadmin=context.is_superadmin,
        roles=sorted(context.roles),
        permissions=sorted(context.permissions),
    )


@router.get(
    "/settings",
    response_model=list[ApplicationSettingResponse],
)
async def list_application_settings(
    actor_telegram_id: int = Query(),
    category: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[ApplicationSettingResponse]:
    try:
        await AdminAccessService(db).require_permission(
            actor_telegram_id,
            PermissionCode.SETTINGS_VIEW,
        )
        rows = await ApplicationSettingsService(db).list_settings(
            category=category
        )
        return [serialize_setting(row) for row in rows]
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SettingValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put(
    "/settings/{key}",
    response_model=ApplicationSettingResponse,
)
async def update_application_setting(
    key: str,
    data: ApplicationSettingUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApplicationSettingResponse:
    try:
        context = await AdminAccessService(db).require_permission(
            data.actor_telegram_id,
            PermissionCode.SETTINGS_MANAGE,
        )
        if context.user_id is None:
            raise AdminAccessDenied("Administrator user is not registered")

        setting = await ApplicationSettingsService(db).set_value(
            key=key,
            category=data.category,
            value=data.value,
            is_sensitive=data.is_sensitive,
            actor_user_id=context.user_id,
            actor_telegram_id=data.actor_telegram_id,
            description=data.description,
            expected_version=data.expected_version,
        )
        await db.commit()
        await db.refresh(setting)
        return serialize_setting(setting)
    except AdminAccessDenied as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SettingConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SettingValidationError, SettingEncryptionError) as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
