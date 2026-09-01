import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import (
    AdminAccount,
    AdminPermission,
    AdminRole,
    AdminRoleAssignment,
    AdminRolePermission,
)
from app.models.user import User, UserStatus
from app.services.admin_access import (
    AdminAccessService,
    AdminContext,
    PermissionCode,
)
from app.services.audit import AuditService


ROLE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,99}$")


class AdminManagementError(ValueError):
    code = "admin_management_error"


class AdminTargetNotFound(AdminManagementError):
    code = "admin_target_not_found"


class AdminAccountNotFound(AdminManagementError):
    code = "admin_account_not_found"


class AdminAccountConflict(AdminManagementError):
    code = "admin_account_conflict"


class LastSuperadminError(AdminManagementError):
    code = "last_superadmin"


class AdminRoleNotFound(AdminManagementError):
    code = "admin_role_not_found"


class AdminRoleConflict(AdminManagementError):
    code = "admin_role_conflict"


class AdminRoleValidationError(AdminManagementError):
    code = "admin_role_validation"


class AdminRoleInUse(AdminManagementError):
    code = "admin_role_in_use"


class SystemRoleProtected(AdminManagementError):
    code = "system_role_protected"


@dataclass(frozen=True, slots=True)
class AdminAccountRecord:
    account: AdminAccount
    user: User
    roles: tuple[AdminRole, ...]


@dataclass(frozen=True, slots=True)
class AdminRoleRecord:
    role: AdminRole
    permission_codes: tuple[str, ...]
    assignment_count: int


class AdminManagementService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.access = AdminAccessService(session)

    @staticmethod
    def normalize_reason(reason: str) -> str:
        normalized = str(reason or "").strip()

        if not 3 <= len(normalized) <= 500:
            raise AdminRoleValidationError(
                "Change reason must contain between 3 and 500 characters"
            )

        return normalized

    @staticmethod
    def normalize_role_code(code: str) -> str:
        normalized = str(code or "").strip().lower()

        if not ROLE_CODE_PATTERN.fullmatch(normalized):
            raise AdminRoleValidationError(
                "Role code must start with a letter and contain only "
                "lowercase letters, numbers, dots, underscores, or hyphens"
            )

        return normalized

    @staticmethod
    def normalize_role_name(name: str) -> str:
        normalized = str(name or "").strip()

        if not 2 <= len(normalized) <= 150:
            raise AdminRoleValidationError(
                "Role name must contain between 2 and 150 characters"
            )

        return normalized

    @staticmethod
    def normalize_description(description: str | None) -> str | None:
        if description is None:
            return None

        normalized = str(description).strip()

        if len(normalized) > 2000:
            raise AdminRoleValidationError(
                "Role description cannot exceed 2000 characters"
            )

        return normalized or None

    async def _require_admin_manager(
        self,
        actor_telegram_id: int,
    ) -> AdminContext:
        return await self.access.require_permission(
            actor_telegram_id,
            PermissionCode.ADMINS_MANAGE,
        )

    async def _require_role_manager(
        self,
        actor_telegram_id: int,
    ) -> AdminContext:
        return await self.access.require_permission(
            actor_telegram_id,
            PermissionCode.ROLES_MANAGE,
        )

    async def _require_role_reader(
        self,
        actor_telegram_id: int,
    ) -> AdminContext:
        return await self.access.require_any_permission(
            actor_telegram_id,
            PermissionCode.ADMINS_MANAGE,
            PermissionCode.ROLES_MANAGE,
        )

    async def _lock_active_superadmin_ids(self) -> tuple[int, ...]:
        result = await self.session.execute(
            select(AdminAccount.id)
            .where(
                AdminAccount.is_active.is_(True),
                AdminAccount.is_superadmin.is_(True),
            )
            .order_by(AdminAccount.id)
            .with_for_update()
        )
        return tuple(result.scalars().all())

    async def _get_roles_for_account(
        self,
        account_id: int,
    ) -> tuple[AdminRole, ...]:
        result = await self.session.execute(
            select(AdminRole)
            .join(
                AdminRoleAssignment,
                AdminRoleAssignment.role_id == AdminRole.id,
            )
            .where(AdminRoleAssignment.admin_account_id == account_id)
            .order_by(AdminRole.name, AdminRole.code)
        )
        return tuple(result.scalars().all())

    async def _build_account_record(
        self,
        account: AdminAccount,
        user: User,
    ) -> AdminAccountRecord:
        return AdminAccountRecord(
            account=account,
            user=user,
            roles=await self._get_roles_for_account(account.id),
        )

    async def _resolve_active_roles(
        self,
        role_codes: list[str] | tuple[str, ...],
        *,
        lock: bool,
    ) -> tuple[AdminRole, ...]:
        normalized_codes = tuple(
            dict.fromkeys(
                self.normalize_role_code(code)
                for code in role_codes
            )
        )

        if not normalized_codes:
            return ()

        statement = (
            select(AdminRole)
            .where(
                AdminRole.code.in_(normalized_codes),
                AdminRole.is_active.is_(True),
            )
            .order_by(AdminRole.code)
        )

        if lock:
            statement = statement.with_for_update()

        result = await self.session.execute(statement)
        roles = tuple(result.scalars().all())
        found_codes = {role.code for role in roles}
        missing_codes = [
            code for code in normalized_codes if code not in found_codes
        ]

        if missing_codes:
            raise AdminRoleNotFound(
                "Unknown or inactive roles: " + ", ".join(missing_codes)
            )

        return roles

    async def _roles_grant_admin_access(
        self,
        role_ids: tuple[int, ...],
    ) -> bool:
        if not role_ids:
            return False

        result = await self.session.execute(
            select(AdminPermission.id)
            .join(
                AdminRolePermission,
                AdminRolePermission.permission_id == AdminPermission.id,
            )
            .where(
                AdminRolePermission.role_id.in_(role_ids),
                AdminPermission.code == PermissionCode.ADMIN_ACCESS,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _account_has_admin_access(self, account_id: int) -> bool:
        result = await self.session.execute(
            select(AdminPermission.id)
            .join(
                AdminRolePermission,
                AdminRolePermission.permission_id == AdminPermission.id,
            )
            .join(AdminRole, AdminRole.id == AdminRolePermission.role_id)
            .join(
                AdminRoleAssignment,
                AdminRoleAssignment.role_id == AdminRole.id,
            )
            .where(
                AdminRoleAssignment.admin_account_id == account_id,
                AdminRole.is_active.is_(True),
                AdminPermission.code == PermissionCode.ADMIN_ACCESS,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _replace_assignments(
        self,
        account: AdminAccount,
        roles: tuple[AdminRole, ...],
        actor_user_id: int,
    ) -> None:
        await self.session.execute(
            delete(AdminRoleAssignment).where(
                AdminRoleAssignment.admin_account_id == account.id
            )
        )

        for role in roles:
            self.session.add(
                AdminRoleAssignment(
                    admin_account_id=account.id,
                    role_id=role.id,
                    assigned_by_user_id=actor_user_id,
                )
            )

    async def list_accounts(
        self,
        *,
        actor_telegram_id: int,
        offset: int = 0,
        limit: int = 50,
    ) -> list[AdminAccountRecord]:
        await self._require_admin_manager(actor_telegram_id)
        result = await self.session.execute(
            select(AdminAccount, User)
            .join(User, User.id == AdminAccount.user_id)
            .order_by(
                AdminAccount.is_active.desc(),
                AdminAccount.is_superadmin.desc(),
                AdminAccount.id,
            )
            .offset(offset)
            .limit(limit)
        )

        return [
            await self._build_account_record(account, user)
            for account, user in result.all()
        ]

    async def get_account(
        self,
        *,
        actor_telegram_id: int,
        target_telegram_id: int,
    ) -> AdminAccountRecord:
        await self._require_admin_manager(actor_telegram_id)
        result = await self.session.execute(
            select(AdminAccount, User)
            .join(User, User.id == AdminAccount.user_id)
            .where(User.telegram_id == target_telegram_id)
        )
        row = result.first()

        if row is None:
            raise AdminAccountNotFound("Administrator account not found")

        return await self._build_account_record(*row)

    async def create_account(
        self,
        *,
        actor_telegram_id: int,
        target_telegram_id: int,
        role_codes: list[str],
        is_superadmin: bool,
        reason: str,
    ) -> AdminAccountRecord:
        actor = await self._require_admin_manager(actor_telegram_id)
        normalized_reason = self.normalize_reason(reason)

        if actor.user_id is None:
            raise AdminManagementError("Administrator user is not registered")

        await self._lock_active_superadmin_ids()
        user_result = await self.session.execute(
            select(User)
            .where(User.telegram_id == target_telegram_id)
            .with_for_update()
        )
        user = user_result.scalar_one_or_none()

        if user is None or user.status != UserStatus.ACTIVE:
            raise AdminTargetNotFound(
                "Target user must start the bot and have an active account"
            )

        account_result = await self.session.execute(
            select(AdminAccount)
            .where(AdminAccount.user_id == user.id)
            .with_for_update()
        )
        account = account_result.scalar_one_or_none()

        if account is not None and account.is_active:
            raise AdminAccountConflict("Administrator account is already active")

        roles = await self._resolve_active_roles(role_codes, lock=True)

        if not is_superadmin and not await self._roles_grant_admin_access(
            tuple(role.id for role in roles)
        ):
            raise AdminRoleValidationError(
                "A non-superadmin must receive at least one role that grants "
                "admin.access"
            )

        reactivated = account is not None

        if account is None:
            account = AdminAccount(
                user_id=user.id,
                created_by_user_id=actor.user_id,
            )
            self.session.add(account)
            await self.session.flush()

        account.is_active = True
        account.is_superadmin = is_superadmin
        account.deactivated_at = None
        user.is_admin = True
        await self._replace_assignments(account, roles, actor.user_id)

        AuditService(self.session).record(
            action=(
                "admin.account_reactivated"
                if reactivated
                else "admin.account_created"
            ),
            actor_user_id=actor.user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="admin_account",
            target_id=account.id,
            details={
                "target_telegram_id": target_telegram_id,
                "is_superadmin": is_superadmin,
                "roles": [role.code for role in roles],
                "reason": normalized_reason,
            },
        )
        await self.session.flush()
        return await self._build_account_record(account, user)

    async def update_account(
        self,
        *,
        actor_telegram_id: int,
        target_telegram_id: int,
        reason: str,
        role_codes: list[str] | None = None,
        is_superadmin: bool | None = None,
        is_active: bool | None = None,
    ) -> AdminAccountRecord:
        actor = await self._require_admin_manager(actor_telegram_id)
        normalized_reason = self.normalize_reason(reason)

        if actor.user_id is None:
            raise AdminManagementError("Administrator user is not registered")

        active_superadmin_ids = await self._lock_active_superadmin_ids()
        result = await self.session.execute(
            select(AdminAccount, User)
            .join(User, User.id == AdminAccount.user_id)
            .where(User.telegram_id == target_telegram_id)
            .with_for_update()
        )
        row = result.first()

        if row is None:
            raise AdminAccountNotFound("Administrator account not found")

        account, user = row
        previous_roles = await self._get_roles_for_account(account.id)
        next_is_active = account.is_active if is_active is None else is_active
        next_is_superadmin = (
            account.is_superadmin
            if is_superadmin is None
            else is_superadmin
        )

        removes_active_superadmin = (
            account.id in active_superadmin_ids
            and (not next_is_active or not next_is_superadmin)
        )

        if removes_active_superadmin and len(active_superadmin_ids) <= 1:
            raise LastSuperadminError(
                "The last active superadmin cannot be deactivated or demoted"
            )

        roles: tuple[AdminRole, ...] | None = None

        if role_codes is not None:
            roles = await self._resolve_active_roles(role_codes, lock=True)

            if (
                next_is_active
                and not next_is_superadmin
                and not await self._roles_grant_admin_access(
                    tuple(role.id for role in roles)
                )
            ):
                raise AdminRoleValidationError(
                    "A non-superadmin must receive at least one role that "
                    "grants admin.access"
                )
        elif (
            next_is_active
            and not next_is_superadmin
            and not await self._account_has_admin_access(account.id)
        ):
            raise AdminRoleValidationError(
                "A non-superadmin must retain a role that grants admin.access"
            )

        before = {
            "is_active": account.is_active,
            "is_superadmin": account.is_superadmin,
            "roles": [role.code for role in previous_roles],
        }
        account.is_active = next_is_active
        account.is_superadmin = next_is_superadmin
        account.deactivated_at = (
            None if next_is_active else datetime.now(timezone.utc)
        )
        user.is_admin = next_is_active

        if roles is not None:
            await self._replace_assignments(account, roles, actor.user_id)

        AuditService(self.session).record(
            action="admin.account_updated",
            actor_user_id=actor.user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="admin_account",
            target_id=account.id,
            details={
                "target_telegram_id": target_telegram_id,
                "before": before,
                "after": {
                    "is_active": next_is_active,
                    "is_superadmin": next_is_superadmin,
                    "roles": (
                        [role.code for role in roles]
                        if roles is not None
                        else before["roles"]
                    ),
                },
                "reason": normalized_reason,
            },
        )
        await self.session.flush()
        return await self._build_account_record(account, user)

    async def list_permissions(
        self,
        *,
        actor_telegram_id: int,
    ) -> list[AdminPermission]:
        await self._require_role_manager(actor_telegram_id)
        result = await self.session.execute(
            select(AdminPermission).order_by(AdminPermission.code)
        )
        return list(result.scalars().all())

    async def _build_role_records(
        self,
        roles: list[AdminRole],
    ) -> list[AdminRoleRecord]:
        if not roles:
            return []

        role_ids = [role.id for role in roles]
        permission_result = await self.session.execute(
            select(AdminRolePermission.role_id, AdminPermission.code)
            .join(
                AdminPermission,
                AdminPermission.id == AdminRolePermission.permission_id,
            )
            .where(AdminRolePermission.role_id.in_(role_ids))
            .order_by(AdminPermission.code)
        )
        permissions_by_role: dict[int, list[str]] = {
            role_id: [] for role_id in role_ids
        }

        for role_id, permission_code in permission_result.all():
            permissions_by_role[role_id].append(permission_code)

        count_result = await self.session.execute(
            select(
                AdminRoleAssignment.role_id,
                func.count(AdminRoleAssignment.id),
            )
            .where(AdminRoleAssignment.role_id.in_(role_ids))
            .group_by(AdminRoleAssignment.role_id)
        )
        counts = dict(count_result.all())

        return [
            AdminRoleRecord(
                role=role,
                permission_codes=tuple(permissions_by_role[role.id]),
                assignment_count=int(counts.get(role.id, 0)),
            )
            for role in roles
        ]

    async def list_roles(
        self,
        *,
        actor_telegram_id: int,
        include_inactive: bool = True,
    ) -> list[AdminRoleRecord]:
        await self._require_role_reader(actor_telegram_id)
        statement = select(AdminRole).order_by(
            AdminRole.is_active.desc(),
            AdminRole.is_system.desc(),
            AdminRole.name,
        )

        if not include_inactive:
            statement = statement.where(AdminRole.is_active.is_(True))

        result = await self.session.execute(statement)
        return await self._build_role_records(list(result.scalars().all()))

    async def _resolve_permissions(
        self,
        permission_codes: list[str],
    ) -> tuple[AdminPermission, ...]:
        normalized_codes = tuple(
            dict.fromkeys(
                str(code or "").strip().lower()
                for code in permission_codes
                if str(code or "").strip()
            )
        )

        if not normalized_codes:
            raise AdminRoleValidationError(
                "A role must contain at least one permission"
            )

        result = await self.session.execute(
            select(AdminPermission)
            .where(AdminPermission.code.in_(normalized_codes))
            .order_by(AdminPermission.code)
            .with_for_update()
        )
        permissions = tuple(result.scalars().all())
        found_codes = {permission.code for permission in permissions}
        missing = [
            code for code in normalized_codes if code not in found_codes
        ]

        if missing:
            raise AdminRoleValidationError(
                "Unknown permissions: " + ", ".join(missing)
            )

        return permissions

    async def _replace_role_permissions(
        self,
        role: AdminRole,
        permissions: tuple[AdminPermission, ...],
    ) -> None:
        await self.session.execute(
            delete(AdminRolePermission).where(
                AdminRolePermission.role_id == role.id
            )
        )

        for permission in permissions:
            self.session.add(
                AdminRolePermission(
                    role_id=role.id,
                    permission_id=permission.id,
                )
            )

    async def _ensure_role_change_keeps_admin_access(
        self,
        *,
        role_id: int,
        remains_active: bool,
        permission_codes: set[str],
    ) -> None:
        if remains_active and PermissionCode.ADMIN_ACCESS in permission_codes:
            return

        affected_result = await self.session.execute(
            select(AdminAccount.id)
            .join(
                AdminRoleAssignment,
                AdminRoleAssignment.admin_account_id == AdminAccount.id,
            )
            .where(
                AdminRoleAssignment.role_id == role_id,
                AdminAccount.is_active.is_(True),
                AdminAccount.is_superadmin.is_(False),
            )
        )
        affected_ids = set(affected_result.scalars().all())

        if not affected_ids:
            return

        protected_result = await self.session.execute(
            select(AdminRoleAssignment.admin_account_id)
            .join(AdminRole, AdminRole.id == AdminRoleAssignment.role_id)
            .join(
                AdminRolePermission,
                AdminRolePermission.role_id == AdminRole.id,
            )
            .join(
                AdminPermission,
                AdminPermission.id == AdminRolePermission.permission_id,
            )
            .where(
                AdminRoleAssignment.admin_account_id.in_(affected_ids),
                AdminRoleAssignment.role_id != role_id,
                AdminRole.is_active.is_(True),
                AdminPermission.code == PermissionCode.ADMIN_ACCESS,
            )
            .distinct()
        )
        protected_ids = set(protected_result.scalars().all())
        stranded_ids = affected_ids - protected_ids

        if stranded_ids:
            raise AdminRoleInUse(
                "This change would remove admin.access from "
                f"{len(stranded_ids)} active administrator(s); reassign "
                "them before changing this role"
            )

    async def create_role(
        self,
        *,
        actor_telegram_id: int,
        code: str,
        name: str,
        description: str | None,
        permission_codes: list[str],
        reason: str,
    ) -> AdminRoleRecord:
        actor = await self._require_role_manager(actor_telegram_id)
        normalized_code = self.normalize_role_code(code)
        normalized_name = self.normalize_role_name(name)
        normalized_description = self.normalize_description(description)
        normalized_reason = self.normalize_reason(reason)

        if actor.user_id is None:
            raise AdminManagementError("Administrator user is not registered")

        existing_result = await self.session.execute(
            select(AdminRole)
            .where(AdminRole.code == normalized_code)
            .with_for_update()
        )

        if existing_result.scalar_one_or_none() is not None:
            raise AdminRoleConflict("Role code already exists")

        permissions = await self._resolve_permissions(permission_codes)
        role = AdminRole(
            code=normalized_code,
            name=normalized_name,
            description=normalized_description,
            is_system=False,
            is_active=True,
        )
        self.session.add(role)
        await self.session.flush()
        await self._replace_role_permissions(role, permissions)
        AuditService(self.session).record(
            action="admin.role_created",
            actor_user_id=actor.user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="admin_role",
            target_id=role.id,
            details={
                "code": role.code,
                "name": role.name,
                "permissions": [
                    permission.code for permission in permissions
                ],
                "reason": normalized_reason,
            },
        )
        await self.session.flush()
        return (await self._build_role_records([role]))[0]

    async def update_role(
        self,
        *,
        actor_telegram_id: int,
        role_id: int,
        reason: str,
        name: str | None = None,
        description: str | None = None,
        description_supplied: bool = False,
        permission_codes: list[str] | None = None,
        is_active: bool | None = None,
    ) -> AdminRoleRecord:
        actor = await self._require_role_manager(actor_telegram_id)
        normalized_reason = self.normalize_reason(reason)

        if actor.user_id is None:
            raise AdminManagementError("Administrator user is not registered")

        result = await self.session.execute(
            select(AdminRole)
            .where(AdminRole.id == role_id)
            .with_for_update()
        )
        role = result.scalar_one_or_none()

        if role is None:
            raise AdminRoleNotFound("Role not found")

        next_is_active = role.is_active if is_active is None else is_active

        if role.is_system and not next_is_active:
            raise SystemRoleProtected("Built-in roles cannot be deactivated")

        current_record = (await self._build_role_records([role]))[0]
        permissions: tuple[AdminPermission, ...] | None = None
        next_permission_codes = set(current_record.permission_codes)

        if permission_codes is not None:
            permissions = await self._resolve_permissions(permission_codes)
            next_permission_codes = {
                permission.code for permission in permissions
            }

        await self._ensure_role_change_keeps_admin_access(
            role_id=role.id,
            remains_active=next_is_active,
            permission_codes=next_permission_codes,
        )
        before = {
            "name": role.name,
            "description": role.description,
            "is_active": role.is_active,
            "permissions": list(current_record.permission_codes),
        }

        if name is not None:
            role.name = self.normalize_role_name(name)

        if description_supplied:
            role.description = self.normalize_description(description)

        role.is_active = next_is_active

        if permissions is not None:
            await self._replace_role_permissions(role, permissions)

        AuditService(self.session).record(
            action="admin.role_updated",
            actor_user_id=actor.user_id,
            actor_telegram_id=actor_telegram_id,
            target_type="admin_role",
            target_id=role.id,
            details={
                "code": role.code,
                "before": before,
                "after": {
                    "name": role.name,
                    "description": role.description,
                    "is_active": role.is_active,
                    "permissions": sorted(next_permission_codes),
                },
                "reason": normalized_reason,
            },
        )
        await self.session.flush()
        return (await self._build_role_records([role]))[0]
