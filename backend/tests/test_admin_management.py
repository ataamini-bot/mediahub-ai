import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token")


from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models.admin import AdminAccount  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.user import User, UserStatus  # noqa: E402
from app.services.admin_management import (  # noqa: E402
    AdminManagementService,
    AdminRoleInUse,
    LastSuperadminError,
)


def unique_telegram_id() -> int:
    return 8_000_000_000_000 + uuid.uuid4().int % 100_000_000


async def add_user(session, telegram_id: int) -> User:
    user = User(
        telegram_id=telegram_id,
        first_name="Admin test",
        status=UserStatus.ACTIVE,
        is_admin=False,
    )
    session.add(user)
    await session.flush()
    return user


async def add_superadmin(session, telegram_id: int) -> tuple[User, AdminAccount]:
    user = await add_user(session, telegram_id)
    account = AdminAccount(
        user_id=user.id,
        is_superadmin=True,
        is_active=True,
        created_by_user_id=user.id,
    )
    user.is_admin = True
    session.add(account)
    await session.flush()
    return user, account


@pytest.mark.asyncio
async def test_last_active_superadmin_cannot_be_demoted():
    telegram_id = unique_telegram_id()

    async with AsyncSessionLocal() as session:
        transaction = await session.begin()

        try:
            await add_superadmin(session, telegram_id)

            with pytest.raises(LastSuperadminError):
                await AdminManagementService(session).update_account(
                    actor_telegram_id=telegram_id,
                    target_telegram_id=telegram_id,
                    is_superadmin=False,
                    reason="Safety regression test",
                )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_admin_can_receive_multiple_roles_and_audit_reason():
    actor_telegram_id = unique_telegram_id()
    target_telegram_id = unique_telegram_id()

    async with AsyncSessionLocal() as session:
        transaction = await session.begin()

        try:
            await add_superadmin(session, actor_telegram_id)
            target = await add_user(session, target_telegram_id)
            reason = "Delegate support and broadcast work"
            record = await AdminManagementService(session).create_account(
                actor_telegram_id=actor_telegram_id,
                target_telegram_id=target_telegram_id,
                role_codes=["support", "broadcast_content"],
                is_superadmin=False,
                reason=reason,
            )

            assert {role.code for role in record.roles} == {
                "support",
                "broadcast_content",
            }
            assert record.account.is_active is True
            assert record.account.is_superadmin is False
            assert target.is_admin is True

            audit_result = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "admin.account_created",
                    AuditLog.target_id == str(record.account.id),
                )
            )
            audit_log = audit_result.scalar_one()
            assert audit_log.details["reason"] == reason
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_role_cannot_strand_active_administrator():
    actor_telegram_id = unique_telegram_id()
    target_telegram_id = unique_telegram_id()

    async with AsyncSessionLocal() as session:
        transaction = await session.begin()

        try:
            await add_superadmin(session, actor_telegram_id)
            await add_user(session, target_telegram_id)
            service = AdminManagementService(session)
            role = await service.create_role(
                actor_telegram_id=actor_telegram_id,
                code=f"test_role_{uuid.uuid4().hex[:10]}",
                name="Test operator",
                description=None,
                permission_codes=["admin.access", "tickets.view"],
                reason="Create isolated role for safety test",
            )
            await service.create_account(
                actor_telegram_id=actor_telegram_id,
                target_telegram_id=target_telegram_id,
                role_codes=[role.role.code],
                is_superadmin=False,
                reason="Assign isolated role for safety test",
            )

            with pytest.raises(AdminRoleInUse):
                await service.update_role(
                    actor_telegram_id=actor_telegram_id,
                    role_id=role.role.id,
                    permission_codes=["tickets.view"],
                    reason="Attempt unsafe permission removal",
                )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_concurrent_demotions_preserve_one_superadmin():
    first_telegram_id = unique_telegram_id()
    second_telegram_id = unique_telegram_id()
    telegram_ids = [first_telegram_id, second_telegram_id]

    async with AsyncSessionLocal() as setup_session:
        await add_superadmin(setup_session, first_telegram_id)
        await add_superadmin(setup_session, second_telegram_id)
        await setup_session.commit()

    async def demote_self(telegram_id: int) -> str:
        async with AsyncSessionLocal() as session:
            try:
                await AdminManagementService(session).update_account(
                    actor_telegram_id=telegram_id,
                    target_telegram_id=telegram_id,
                    is_superadmin=False,
                    reason="Concurrent demotion safety test",
                )
                await session.commit()
                return "demoted"
            except LastSuperadminError:
                await session.rollback()
                return "protected"

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                demote_self(first_telegram_id),
                demote_self(second_telegram_id),
            ),
            timeout=10,
        )

        assert sorted(outcomes) == ["demoted", "protected"]

        async with AsyncSessionLocal() as verify_session:
            result = await verify_session.execute(
                select(AdminAccount)
                .join(User, User.id == AdminAccount.user_id)
                .where(User.telegram_id.in_(telegram_ids))
            )
            accounts = list(result.scalars().all())
            assert sum(
                account.is_active and account.is_superadmin
                for account in accounts
            ) == 1
    finally:
        async with AsyncSessionLocal() as cleanup_session:
            await cleanup_session.execute(
                delete(AuditLog).where(
                    AuditLog.actor_telegram_id.in_(telegram_ids)
                )
            )
            await cleanup_session.execute(
                delete(User).where(User.telegram_id.in_(telegram_ids))
            )
            await cleanup_session.commit()
