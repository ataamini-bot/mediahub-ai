from dataclasses import dataclass

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.admin import (
    AdminAccount,
    AdminPermission,
    AdminRole,
    AdminRoleAssignment,
    AdminRolePermission,
)
from app.models.user import User
from app.services.audit import AuditService


class PermissionCode:
    ADMIN_ACCESS = "admin.access"
    ADMINS_MANAGE = "admins.manage"
    ROLES_MANAGE = "roles.manage"
    SETTINGS_VIEW = "settings.view"
    SETTINGS_MANAGE = "settings.manage"
    PAYMENTS_VIEW = "payments.view"
    PAYMENTS_REVIEW = "payments.review"
    PAYMENT_DESTINATIONS_MANAGE = "payment_destinations.manage"
    PLANS_MANAGE = "plans.manage"
    AUDIT_VIEW = "audit.view"


class AdminAccessDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class AdminContext:
    telegram_id: int
    user_id: int | None
    admin_account_id: int | None
    is_admin: bool
    is_superadmin: bool
    roles: frozenset[str]
    permissions: frozenset[str]

    def has_permission(self, permission: str) -> bool:
        return self.is_superadmin or permission in self.permissions


class AdminAccessService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_bootstrap_superadmin(self, user: User) -> AdminAccount | None:
        """Create the initial database authority without re-demoting admins.

        The environment value is only a bootstrap escape hatch.  Once an
        account exists, normal role management is database-backed.
        """
        result = await self.session.execute(
            select(AdminAccount)
            .where(AdminAccount.user_id == user.id)
            .with_for_update()
        )
        admin_account = result.scalar_one_or_none()
        is_bootstrap_identity = (
            user.telegram_id in settings.bootstrap_superadmin_id_set
        )

        if admin_account is None and is_bootstrap_identity:
            admin_account = AdminAccount(
                user_id=user.id,
                is_superadmin=True,
                is_active=True,
                created_by_user_id=user.id,
            )
            self.session.add(admin_account)
            user.is_admin = True
            AuditService(self.session).record(
                action="admin.bootstrap_created",
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_id,
                target_type="admin_account",
                target_id=user.id,
                details={"is_superadmin": True},
            )
            return admin_account

        if admin_account is not None:
            user.is_admin = admin_account.is_active
        else:
            user.is_admin = False

        return admin_account

    async def get_context(self, telegram_id: int) -> AdminContext:
        result = await self.session.execute(
            select(User, AdminAccount)
            .outerjoin(AdminAccount, AdminAccount.user_id == User.id)
            .where(User.telegram_id == telegram_id)
        )
        row = result.first()

        if row is None:
            return AdminContext(
                telegram_id=telegram_id,
                user_id=None,
                admin_account_id=None,
                is_admin=False,
                is_superadmin=False,
                roles=frozenset(),
                permissions=frozenset(),
            )

        user, admin_account = row

        if admin_account is None or not admin_account.is_active:
            return AdminContext(
                telegram_id=telegram_id,
                user_id=user.id,
                admin_account_id=(
                    admin_account.id if admin_account is not None else None
                ),
                is_admin=False,
                is_superadmin=False,
                roles=frozenset(),
                permissions=frozenset(),
            )

        roles_result = await self.session.execute(
            select(distinct(AdminRole.code))
            .join(
                AdminRoleAssignment,
                AdminRoleAssignment.role_id == AdminRole.id,
            )
            .where(
                AdminRoleAssignment.admin_account_id == admin_account.id,
                AdminRole.is_active.is_(True),
            )
        )
        roles = frozenset(roles_result.scalars().all())

        if admin_account.is_superadmin:
            permission_result = await self.session.execute(
                select(AdminPermission.code)
            )
        else:
            permission_result = await self.session.execute(
                select(distinct(AdminPermission.code))
                .join(
                    AdminRolePermission,
                    AdminRolePermission.permission_id == AdminPermission.id,
                )
                .join(
                    AdminRole,
                    AdminRole.id == AdminRolePermission.role_id,
                )
                .join(
                    AdminRoleAssignment,
                    AdminRoleAssignment.role_id == AdminRole.id,
                )
                .where(
                    AdminRoleAssignment.admin_account_id == admin_account.id,
                    AdminRole.is_active.is_(True),
                )
            )

        return AdminContext(
            telegram_id=telegram_id,
            user_id=user.id,
            admin_account_id=admin_account.id,
            is_admin=True,
            is_superadmin=admin_account.is_superadmin,
            roles=roles,
            permissions=frozenset(permission_result.scalars().all()),
        )

    async def require_permission(
        self,
        telegram_id: int,
        permission: str,
    ) -> AdminContext:
        context = await self.get_context(telegram_id)

        if not context.is_admin or not context.has_permission(permission):
            raise AdminAccessDenied(
                f"Administrator permission required: {permission}"
            )

        return context

    async def require_any_permission(
        self,
        telegram_id: int,
        *permissions: str,
    ) -> AdminContext:
        if not permissions:
            raise ValueError("At least one permission must be supplied")

        context = await self.get_context(telegram_id)

        if not context.is_admin or not any(
            context.has_permission(permission)
            for permission in permissions
        ):
            required = ", ".join(permissions)
            raise AdminAccessDenied(
                f"One administrator permission is required: {required}"
            )

        return context
