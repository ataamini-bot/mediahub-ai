"""add database-backed admin foundation

Revision ID: c8f4d1a2b7e9
Revises: f4c2a91d7e3b
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f4d1a2b7e9"
down_revision: Union[str, None] = "f4c2a91d7e3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PERMISSIONS = (
    ("admin.access", "Open the Telegram administration panel"),
    ("admins.manage", "Create, update, and deactivate administrators"),
    ("roles.manage", "Create roles and assign permissions"),
    ("settings.view", "View non-sensitive application settings"),
    ("settings.manage", "Update application settings"),
    ("payments.view", "View payment orders and proofs"),
    ("payments.review", "Approve or reject manual payments"),
    ("payment_destinations.manage", "Manage bank cards and USDT destinations"),
    ("plans.manage", "Manage plans, prices, and technical limits"),
    ("users.view", "Search and view users"),
    ("users.manage", "Block users and update user metadata"),
    ("subscriptions.manage", "Grant, change, or revoke subscriptions"),
    ("balances.manage", "Credit or debit internal balances"),
    ("coupons.manage", "Manage discount codes"),
    ("tickets.view", "View support tickets"),
    ("tickets.reply", "Reply to and update support tickets"),
    ("tickets.manage", "Assign and administrate support tickets"),
    ("broadcasts.manage", "Create and operate broadcasts"),
    ("forced_join.manage", "Manage required membership channels"),
    ("monitoring.view", "View monitoring status"),
    ("monitoring.manage", "Manage monitoring and notification settings"),
    ("backups.view", "View backup status and inventory"),
    ("backups.manage", "Start and configure backups"),
    ("backups.restore", "Restore an encrypted backup"),
    ("audit.view", "View administrative audit logs"),
)


ROLE_PERMISSIONS = {
    "payment_finance": (
        "admin.access",
        "payments.view",
        "payments.review",
        "payment_destinations.manage",
        "plans.manage",
        "balances.manage",
        "coupons.manage",
    ),
    "user_subscription": (
        "admin.access",
        "users.view",
        "users.manage",
        "subscriptions.manage",
    ),
    "support": (
        "admin.access",
        "users.view",
        "tickets.view",
        "tickets.reply",
        "tickets.manage",
    ),
    "broadcast_content": (
        "admin.access",
        "broadcasts.manage",
    ),
    "technical_monitoring": (
        "admin.access",
        "settings.view",
        "monitoring.view",
        "monitoring.manage",
        "backups.view",
        "backups.manage",
        "audit.view",
    ),
}


ROLE_NAMES = {
    "payment_finance": "Payment and Finance Admin",
    "user_subscription": "User and Subscription Admin",
    "support": "Support Admin",
    "broadcast_content": "Broadcast and Content Admin",
    "technical_monitoring": "Technical and Monitoring Admin",
}


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("preferred_language", sa.String(length=5), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_preferred_language",
        "users",
        "preferred_language IS NULL OR preferred_language IN ('fa', 'en')",
    )

    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_superadmin",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_accounts_user_id",
        "admin_accounts",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "admin_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_roles_code", "admin_roles", ["code"], unique=True)

    op.create_table(
        "admin_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_permissions_code",
        "admin_permissions",
        ["code"],
        unique=True,
    )

    op.create_table(
        "application_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=150), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column(
            "value_json",
            sa.JSON(none_as_null=True),
            nullable=True,
        ),
        sa.Column("encrypted_value", sa.Text(), nullable=True),
        sa.Column(
            "is_sensitive",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT (value_json IS NOT NULL AND encrypted_value IS NOT NULL)",
            name="ck_application_settings_single_value",
        ),
        sa.CheckConstraint(
            "encrypted_value IS NULL OR is_sensitive = TRUE",
            name="ck_application_settings_encrypted_sensitive",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_settings_key",
        "application_settings",
        ["key"],
        unique=True,
    )
    op.create_index(
        "ix_application_settings_category",
        "application_settings",
        ["category"],
        unique=False,
    )

    op.create_table(
        "admin_role_assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("admin_account_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["admin_account_id"],
            ["admin_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["admin_roles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "admin_account_id",
            "role_id",
            name="uq_admin_role_assignments_admin_role",
        ),
    )
    op.create_index(
        "ix_admin_role_assignments_admin_account_id",
        "admin_role_assignments",
        ["admin_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_admin_role_assignments_role_id",
        "admin_role_assignments",
        ["role_id"],
        unique=False,
    )

    op.create_table(
        "admin_role_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["admin_roles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["admin_permissions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_admin_role_permissions_role_permission",
        ),
    )
    op.create_index(
        "ix_admin_role_permissions_role_id",
        "admin_role_permissions",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        "ix_admin_role_permissions_permission_id",
        "admin_role_permissions",
        ["permission_id"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=True),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("success", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index(
        "ix_audit_logs_actor_telegram_id",
        "audit_logs",
        ["actor_telegram_id"],
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index(
        "ix_audit_logs_target",
        "audit_logs",
        ["target_type", "target_id"],
    )

    permission_table = sa.table(
        "admin_permissions",
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    op.bulk_insert(
        permission_table,
        [
            {"code": code, "description": description}
            for code, description in PERMISSIONS
        ],
    )

    role_table = sa.table(
        "admin_roles",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("is_system", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        role_table,
        [
            {
                "code": code,
                "name": ROLE_NAMES[code],
                "description": f"Built-in {ROLE_NAMES[code]} role",
                "is_system": True,
                "is_active": True,
            }
            for code in ROLE_PERMISSIONS
        ],
    )

    connection = op.get_bind()
    role_rows = connection.execute(
        sa.text("SELECT id, code FROM admin_roles")
    ).mappings()
    role_ids = {row["code"]: row["id"] for row in role_rows}
    permission_rows = connection.execute(
        sa.text("SELECT id, code FROM admin_permissions")
    ).mappings()
    permission_ids = {row["code"]: row["id"] for row in permission_rows}

    role_permission_table = sa.table(
        "admin_role_permissions",
        sa.column("role_id", sa.Integer()),
        sa.column("permission_id", sa.Integer()),
    )
    op.bulk_insert(
        role_permission_table,
        [
            {
                "role_id": role_ids[role_code],
                "permission_id": permission_ids[permission_code],
            }
            for role_code, permission_codes in ROLE_PERMISSIONS.items()
            for permission_code in permission_codes
        ],
    )

    # Preserve administrators from the legacy boolean until bootstrap RBAC
    # creates their explicit role assignments.
    op.execute(
        sa.text(
            """
            INSERT INTO admin_accounts (user_id, is_superadmin, is_active)
            SELECT id, TRUE, TRUE
            FROM users
            WHERE is_admin = TRUE
            ON CONFLICT (user_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_target", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_telegram_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(
        "ix_admin_role_permissions_permission_id",
        table_name="admin_role_permissions",
    )
    op.drop_index(
        "ix_admin_role_permissions_role_id",
        table_name="admin_role_permissions",
    )
    op.drop_table("admin_role_permissions")

    op.drop_index(
        "ix_admin_role_assignments_role_id",
        table_name="admin_role_assignments",
    )
    op.drop_index(
        "ix_admin_role_assignments_admin_account_id",
        table_name="admin_role_assignments",
    )
    op.drop_table("admin_role_assignments")

    op.drop_index("ix_application_settings_category", table_name="application_settings")
    op.drop_index("ix_application_settings_key", table_name="application_settings")
    op.drop_table("application_settings")

    op.drop_index("ix_admin_permissions_code", table_name="admin_permissions")
    op.drop_table("admin_permissions")

    op.drop_index("ix_admin_roles_code", table_name="admin_roles")
    op.drop_table("admin_roles")

    op.drop_index("ix_admin_accounts_user_id", table_name="admin_accounts")
    op.drop_table("admin_accounts")

    op.drop_constraint(
        "ck_users_preferred_language",
        "users",
        type_="check",
    )
    op.drop_column("users", "preferred_language")
