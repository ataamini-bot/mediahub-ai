"""add payment destinations and runtime settings

Revision ID: 5d1a9c7e2f40
Revises: e1f6b8c3d920
Create Date: 2026-09-02 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5d1a9c7e2f40"
down_revision: Union[str, None] = "e1f6b8c3d920"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_cards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("card_number", sa.String(length=16), nullable=False),
        sa.Column("card_holder", sa.String(length=120), nullable=False),
        sa.Column("bank_name", sa.String(length=100), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "selection_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
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
            "card_number ~ '^[0-9]{16}$'",
            name="ck_payment_cards_number_digits",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_payment_cards_sort_order_nonnegative",
        ),
        sa.CheckConstraint(
            "selection_count >= 0",
            name="ck_payment_cards_selection_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("card_number"),
    )
    op.create_index(
        "ix_payment_cards_active_order",
        "payment_cards",
        ["is_active", "sort_order", "id"],
        unique=False,
    )

    op.create_table(
        "usdt_destinations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("network_name", sa.String(length=100), nullable=False),
        sa.Column("network_code", sa.String(length=32), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column(
            "asset_symbol",
            sa.String(length=20),
            server_default="USDT",
            nullable=False,
        ),
        sa.Column("contract_address", sa.String(length=255), nullable=True),
        sa.Column("explorer_url", sa.String(length=500), nullable=True),
        sa.Column(
            "confirmations_required",
            sa.Integer(),
            server_default="20",
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "selection_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
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
            "confirmations_required BETWEEN 1 AND 1000",
            name="ck_usdt_destinations_confirmations_range",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_usdt_destinations_sort_order_nonnegative",
        ),
        sa.CheckConstraint(
            "selection_count >= 0",
            name="ck_usdt_destinations_selection_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "network_code",
            "address",
            name="uq_usdt_destinations_network_address",
        ),
    )
    op.create_index(
        "ix_usdt_destinations_active_order",
        "usdt_destinations",
        ["is_active", "sort_order", "id"],
        unique=False,
    )

    op.add_column(
        "payments",
        sa.Column(
            "payment_method",
            sa.String(length=20),
            server_default="card",
            nullable=False,
        ),
    )
    op.add_column(
        "payments",
        sa.Column("payment_card_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column(
            "payment_destination_snapshot",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_payments_payment_card_id_payment_cards",
        "payments",
        "payment_cards",
        ["payment_card_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_payments_payment_card_id",
        "payments",
        ["payment_card_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO application_settings (
                key,
                category,
                value_json,
                is_sensitive,
                description,
                version
            )
            VALUES
                (
                    'bot.maintenance_mode',
                    'bot',
                    'false'::json,
                    FALSE,
                    'Temporarily stop new public operations',
                    1
                ),
                (
                    'downloads.enabled',
                    'downloads',
                    'true'::json,
                    FALSE,
                    'Allow users to create new downloads',
                    1
                ),
                (
                    'payments.enabled',
                    'payments',
                    'true'::json,
                    FALSE,
                    'Allow users to start subscription purchases',
                    1
                ),
                (
                    'payments.receipt_max_size_mb',
                    'payments',
                    '10'::json,
                    FALSE,
                    'Maximum manual payment receipt size in MB',
                    1
                ),
                (
                    'quota.timezone',
                    'quota',
                    '"Asia/Tehran"'::json,
                    FALSE,
                    'Timezone used for daily download quota reset',
                    1
                )
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_payments_payment_card_id", table_name="payments")
    op.drop_constraint(
        "fk_payments_payment_card_id_payment_cards",
        "payments",
        type_="foreignkey",
    )
    op.drop_column("payments", "payment_destination_snapshot")
    op.drop_column("payments", "payment_card_id")
    op.drop_column("payments", "payment_method")
    op.drop_index(
        "ix_usdt_destinations_active_order",
        table_name="usdt_destinations",
    )
    op.drop_table("usdt_destinations")
    op.drop_index("ix_payment_cards_active_order", table_name="payment_cards")
    op.drop_table("payment_cards")
