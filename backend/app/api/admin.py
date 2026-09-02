from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.models.app_setting import ApplicationSetting
from app.schemas.admin import (
    AdminAccountCreate,
    AdminAccountResponse,
    AdminAccountUpdate,
    AdminContextResponse,
    AdminPlanCreate,
    AdminPlanResponse,
    AdminPlanUpdate,
    AdminPermissionResponse,
    AdminRoleCreate,
    AdminRoleResponse,
    AdminRoleSummary,
    AdminRoleUpdate,
    ApplicationSettingResponse,
    ApplicationSettingUpdate,
)
from app.services.admin_access import (
    AdminAccessDenied,
    AdminAccessService,
    PermissionCode,
)
from app.services.admin_management import (
    AdminAccountConflict,
    AdminAccountNotFound,
    AdminAccountRecord,
    AdminManagementError,
    AdminManagementService,
    AdminTargetNotFound,
    AdminRoleConflict,
    AdminRoleInUse,
    AdminRoleNotFound,
    AdminRoleRecord,
    LastSuperadminError,
    SystemRoleProtected,
)
from app.services.application_settings import (
    ApplicationSettingsService,
    SettingConflict,
    SettingEncryptionError,
    SettingValidationError,
)
from app.services.plan_management import (
    PlanConflict,
    PlanManagementError,
    PlanManagementService,
    PlanNotFound,
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


def serialize_admin_account(
    record: AdminAccountRecord,
) -> AdminAccountResponse:
    return AdminAccountResponse(
        account_id=record.account.id,
        user_id=record.user.id,
        telegram_id=record.user.telegram_id,
        username=record.user.username,
        first_name=record.user.first_name,
        last_name=record.user.last_name,
        is_superadmin=record.account.is_superadmin,
        is_active=record.account.is_active,
        roles=[
            AdminRoleSummary(
                id=role.id,
                code=role.code,
                name=role.name,
                is_system=role.is_system,
                is_active=role.is_active,
            )
            for role in record.roles
        ],
        created_at=record.account.created_at,
        updated_at=record.account.updated_at,
        deactivated_at=record.account.deactivated_at,
    )


def serialize_admin_role(record: AdminRoleRecord) -> AdminRoleResponse:
    return AdminRoleResponse(
        id=record.role.id,
        code=record.role.code,
        name=record.role.name,
        description=record.role.description,
        is_system=record.role.is_system,
        is_active=record.role.is_active,
        permission_codes=list(record.permission_codes),
        assignment_count=record.assignment_count,
        created_at=record.role.created_at,
        updated_at=record.role.updated_at,
    )


def serialize_plan(plan) -> AdminPlanResponse:
    return AdminPlanResponse(
        id=plan.id,
        name=plan.name,
        slug=plan.slug,
        description=plan.description,
        price=plan.price,
        duration_days=plan.duration_days,
        daily_download_limit=plan.daily_download_limit,
        max_file_size_mb=plan.max_file_size_mb,
        max_quality=plan.max_quality,
        max_concurrent_downloads=plan.max_concurrent_downloads,
        priority_processing=plan.priority_processing,
        forced_join_required=plan.forced_join_required,
        is_unlimited=plan.is_unlimited,
        sort_order=plan.sort_order,
        is_system=plan.is_system,
        is_active=plan.is_active,
        is_deleted=plan.deleted_at is not None,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def management_http_exception(exc: Exception) -> HTTPException:
    detail = {
        "code": getattr(exc, "code", "admin_management_error"),
        "message": str(exc),
    }

    if isinstance(exc, AdminAccessDenied):
        return HTTPException(status_code=403, detail=detail)

    if isinstance(
        exc,
        (AdminAccountNotFound, AdminRoleNotFound, AdminTargetNotFound),
    ):
        return HTTPException(status_code=404, detail=detail)

    if isinstance(
        exc,
        (
            AdminAccountConflict,
            AdminRoleConflict,
            AdminRoleInUse,
            LastSuperadminError,
            SystemRoleProtected,
            IntegrityError,
        ),
    ):
        if isinstance(exc, IntegrityError):
            detail = {
                "code": "admin_management_conflict",
                "message": "The requested change conflicts with current data",
            }

        return HTTPException(status_code=409, detail=detail)

    return HTTPException(status_code=422, detail=detail)


def plan_http_exception(exc: Exception) -> HTTPException:
    detail = {
        "code": getattr(exc, "code", "plan_management_error"),
        "message": str(exc),
    }

    if isinstance(exc, AdminAccessDenied):
        return HTTPException(status_code=403, detail=detail)

    if isinstance(exc, PlanNotFound):
        return HTTPException(status_code=404, detail=detail)

    if isinstance(exc, (PlanConflict, IntegrityError)):
        return HTTPException(status_code=409, detail=detail)

    return HTTPException(status_code=422, detail=detail)


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


@router.get(
    "/plans",
    response_model=list[AdminPlanResponse],
)
async def list_plans(
    actor_telegram_id: int = Query(),
    include_inactive: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> list[AdminPlanResponse]:
    try:
        await AdminAccessService(db).require_permission(
            actor_telegram_id,
            PermissionCode.PLANS_MANAGE,
        )
        plans = await PlanManagementService(db).list_plans(
            include_inactive=include_inactive,
        )
        return [serialize_plan(plan) for plan in plans]
    except (AdminAccessDenied, PlanManagementError) as exc:
        raise plan_http_exception(exc) from exc


@router.get(
    "/plans/{plan_id}",
    response_model=AdminPlanResponse,
)
async def get_plan(
    plan_id: int,
    actor_telegram_id: int = Query(),
    db: AsyncSession = Depends(get_db),
) -> AdminPlanResponse:
    try:
        await AdminAccessService(db).require_permission(
            actor_telegram_id,
            PermissionCode.PLANS_MANAGE,
        )
        plan = await PlanManagementService(db).get_plan(plan_id)
        return serialize_plan(plan)
    except (AdminAccessDenied, PlanManagementError) as exc:
        raise plan_http_exception(exc) from exc


@router.post(
    "/plans",
    response_model=AdminPlanResponse,
    status_code=201,
)
async def create_plan(
    data: AdminPlanCreate,
    db: AsyncSession = Depends(get_db),
) -> AdminPlanResponse:
    try:
        context = await AdminAccessService(db).require_permission(
            data.actor_telegram_id,
            PermissionCode.PLANS_MANAGE,
        )

        if context.user_id is None:
            raise AdminAccessDenied("Administrator user is not registered")

        plan = await PlanManagementService(db).create_plan(
            actor_user_id=context.user_id,
            actor_telegram_id=data.actor_telegram_id,
            reason=data.reason,
            name=data.name,
            description=data.description,
            duration_days=data.duration_days,
            price=data.price,
            daily_download_limit=data.daily_download_limit,
            max_file_size_mb=data.max_file_size_mb,
            max_quality=data.max_quality,
            max_concurrent_downloads=data.max_concurrent_downloads,
            priority_processing=data.priority_processing,
            forced_join_required=data.forced_join_required,
            sort_order=data.sort_order,
            is_active=data.is_active,
        )
        await db.commit()
        await db.refresh(plan)
        return serialize_plan(plan)
    except (AdminAccessDenied, PlanManagementError, IntegrityError) as exc:
        await db.rollback()
        raise plan_http_exception(exc) from exc


@router.patch(
    "/plans/{plan_id}",
    response_model=AdminPlanResponse,
)
async def update_plan(
    plan_id: int,
    data: AdminPlanUpdate,
    db: AsyncSession = Depends(get_db),
) -> AdminPlanResponse:
    try:
        context = await AdminAccessService(db).require_permission(
            data.actor_telegram_id,
            PermissionCode.PLANS_MANAGE,
        )

        if context.user_id is None:
            raise AdminAccessDenied("Administrator user is not registered")

        plan = await PlanManagementService(db).update_plan(
            plan_id=plan_id,
            actor_user_id=context.user_id,
            actor_telegram_id=data.actor_telegram_id,
            reason=data.reason,
            name=data.name,
            description=data.description,
            description_supplied="description" in data.model_fields_set,
            duration_days=data.duration_days,
            price=data.price,
            daily_download_limit=data.daily_download_limit,
            daily_limit_supplied="daily_download_limit" in data.model_fields_set,
            max_file_size_mb=data.max_file_size_mb,
            max_quality=data.max_quality,
            max_concurrent_downloads=data.max_concurrent_downloads,
            priority_processing=data.priority_processing,
            forced_join_required=data.forced_join_required,
            sort_order=data.sort_order,
            is_active=data.is_active,
            is_deleted=data.is_deleted,
        )
        await db.commit()
        await db.refresh(plan)
        return serialize_plan(plan)
    except (AdminAccessDenied, PlanManagementError, IntegrityError) as exc:
        await db.rollback()
        raise plan_http_exception(exc) from exc


@router.get(
    "/accounts",
    response_model=list[AdminAccountResponse],
)
async def list_admin_accounts(
    actor_telegram_id: int = Query(),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[AdminAccountResponse]:
    try:
        records = await AdminManagementService(db).list_accounts(
            actor_telegram_id=actor_telegram_id,
            offset=offset,
            limit=limit,
        )
        return [serialize_admin_account(record) for record in records]
    except (AdminAccessDenied, AdminManagementError) as exc:
        raise management_http_exception(exc) from exc


@router.get(
    "/accounts/{target_telegram_id}",
    response_model=AdminAccountResponse,
)
async def get_admin_account(
    target_telegram_id: int,
    actor_telegram_id: int = Query(),
    db: AsyncSession = Depends(get_db),
) -> AdminAccountResponse:
    try:
        record = await AdminManagementService(db).get_account(
            actor_telegram_id=actor_telegram_id,
            target_telegram_id=target_telegram_id,
        )
        return serialize_admin_account(record)
    except (AdminAccessDenied, AdminManagementError) as exc:
        raise management_http_exception(exc) from exc


@router.post(
    "/accounts",
    response_model=AdminAccountResponse,
    status_code=201,
)
async def create_admin_account(
    data: AdminAccountCreate,
    db: AsyncSession = Depends(get_db),
) -> AdminAccountResponse:
    try:
        record = await AdminManagementService(db).create_account(
            actor_telegram_id=data.actor_telegram_id,
            target_telegram_id=data.target_telegram_id,
            role_codes=data.role_codes,
            is_superadmin=data.is_superadmin,
            reason=data.reason,
        )
        await db.commit()
        return serialize_admin_account(record)
    except (AdminAccessDenied, AdminManagementError, IntegrityError) as exc:
        await db.rollback()
        raise management_http_exception(exc) from exc


@router.patch(
    "/accounts/{target_telegram_id}",
    response_model=AdminAccountResponse,
)
async def update_admin_account(
    target_telegram_id: int,
    data: AdminAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> AdminAccountResponse:
    try:
        record = await AdminManagementService(db).update_account(
            actor_telegram_id=data.actor_telegram_id,
            target_telegram_id=target_telegram_id,
            reason=data.reason,
            role_codes=data.role_codes,
            is_superadmin=data.is_superadmin,
            is_active=data.is_active,
        )
        await db.commit()
        return serialize_admin_account(record)
    except (AdminAccessDenied, AdminManagementError, IntegrityError) as exc:
        await db.rollback()
        raise management_http_exception(exc) from exc


@router.get(
    "/permissions",
    response_model=list[AdminPermissionResponse],
)
async def list_admin_permissions(
    actor_telegram_id: int = Query(),
    db: AsyncSession = Depends(get_db),
) -> list[AdminPermissionResponse]:
    try:
        permissions = await AdminManagementService(db).list_permissions(
            actor_telegram_id=actor_telegram_id,
        )
        return [
            AdminPermissionResponse(
                id=permission.id,
                code=permission.code,
                description=permission.description,
            )
            for permission in permissions
        ]
    except (AdminAccessDenied, AdminManagementError) as exc:
        raise management_http_exception(exc) from exc


@router.get(
    "/roles",
    response_model=list[AdminRoleResponse],
)
async def list_admin_roles(
    actor_telegram_id: int = Query(),
    include_inactive: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> list[AdminRoleResponse]:
    try:
        records = await AdminManagementService(db).list_roles(
            actor_telegram_id=actor_telegram_id,
            include_inactive=include_inactive,
        )
        return [serialize_admin_role(record) for record in records]
    except (AdminAccessDenied, AdminManagementError) as exc:
        raise management_http_exception(exc) from exc


@router.post(
    "/roles",
    response_model=AdminRoleResponse,
    status_code=201,
)
async def create_admin_role(
    data: AdminRoleCreate,
    db: AsyncSession = Depends(get_db),
) -> AdminRoleResponse:
    try:
        record = await AdminManagementService(db).create_role(
            actor_telegram_id=data.actor_telegram_id,
            code=data.code,
            name=data.name,
            description=data.description,
            permission_codes=data.permission_codes,
            reason=data.reason,
        )
        await db.commit()
        return serialize_admin_role(record)
    except (AdminAccessDenied, AdminManagementError, IntegrityError) as exc:
        await db.rollback()
        raise management_http_exception(exc) from exc


@router.patch(
    "/roles/{role_id}",
    response_model=AdminRoleResponse,
)
async def update_admin_role(
    role_id: int,
    data: AdminRoleUpdate,
    db: AsyncSession = Depends(get_db),
) -> AdminRoleResponse:
    try:
        record = await AdminManagementService(db).update_role(
            actor_telegram_id=data.actor_telegram_id,
            role_id=role_id,
            reason=data.reason,
            name=data.name,
            description=data.description,
            description_supplied="description" in data.model_fields_set,
            permission_codes=data.permission_codes,
            is_active=data.is_active,
        )
        await db.commit()
        return serialize_admin_role(record)
    except (AdminAccessDenied, AdminManagementError, IntegrityError) as exc:
        await db.rollback()
        raise management_http_exception(exc) from exc
