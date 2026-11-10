"""complete manual payment flow

Revision ID: f4c2a91d7e3b
Revises: b693a1fe01f0
Create Date: 2026-08-30 23:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4c2a91d7e3b"
down_revision: Union[str, None] = "b693a1fe01f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("offer_code", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("duration_months", sa.Integer(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("receipt_file_size", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("receipt_mime_type", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("receipt_file_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column(
            "admin_message_thread_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )

    # Preserve any early WIP payment records that may already exist.
    op.execute(
        sa.text(
            """
            UPDATE payments AS payment
            SET duration_months = CASE
                WHEN plan.duration_days >= 330 THEN 12
                WHEN plan.duration_days >= 150 THEN 6
                WHEN plan.duration_days >= 60 THEN 3
                ELSE 1
            END
            FROM plans AS plan
            WHERE payment.plan_id = plan.id
              AND payment.duration_months IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET duration_months = 1
            WHERE duration_months IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET offer_code = CASE duration_months
                WHEN 12 THEN 'premium_12m'
                WHEN 6 THEN 'premium_6m'
                WHEN 3 THEN 'premium_3m'
                ELSE 'premium_1m'
            END
            WHERE offer_code IS NULL
            """
        )
    )

    op.alter_column("payments", "offer_code", nullable=False)
    op.alter_column("payments", "duration_months", nullable=False)
    op.create_check_constraint(
        "ck_payments_duration_months",
        "payments",
        "duration_months IN (1, 3, 6, 12)",
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE payments
            ADD CONSTRAINT ck_payments_amount_positive
            CHECK (amount > 0) NOT VALID
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked_receipts AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY receipt_file_unique_id
                        ORDER BY id
                    ) AS duplicate_number
                FROM payments
                WHERE receipt_file_unique_id IS NOT NULL
            )
            UPDATE payments AS payment
            SET receipt_file_unique_id = NULL
            FROM ranked_receipts AS ranked
            WHERE payment.id = ranked.id
              AND ranked.duplicate_number > 1
            """
        )
    )
    op.create_index(
        "uq_payments_receipt_file_unique_id",
        "payments",
        ["receipt_file_unique_id"],
        unique=True,
    )

    op.add_column(
        "plans",
        sa.Column(
            "is_unlimited",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET is_unlimited = TRUE
            WHERE daily_download_limit IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO plans (
                name,
                slug,
                description,
                price,
                duration_days,
                daily_download_limit,
                max_file_size_mb,
                max_quality,
                ai_enabled,
                priority_processing,
                is_unlimited,
                is_active
            )
            VALUES (
                'Premium',
                'premium',
                'Premium subscription entitlement',
                0,
                30,
                NULL,
                NULL,
                NULL,
                TRUE,
                TRUE,
                TRUE,
                TRUE
            )
            ON CONFLICT (slug) DO UPDATE
            SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                daily_download_limit = EXCLUDED.daily_download_limit,
                max_file_size_mb = EXCLUDED.max_file_size_mb,
                max_quality = EXCLUDED.max_quality,
                ai_enabled = EXCLUDED.ai_enabled,
                priority_processing = EXCLUDED.priority_processing,
                is_unlimited = EXCLUDED.is_unlimited,
                is_active = EXCLUDED.is_active,
                updated_at = now()
            """
        )
    )
    op.alter_column("plans", "is_unlimited", server_default=None)


def downgrade() -> None:
    # Keep the Premium row because payments/subscriptions may reference it.
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET is_active = FALSE
            WHERE slug = 'premium'
            """
        )
    )
    op.drop_column("plans", "is_unlimited")

    op.drop_index(
        "uq_payments_receipt_file_unique_id",
        table_name="payments",
    )
    op.drop_constraint(
        "ck_payments_amount_positive",
        "payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_payments_duration_months",
        "payments",
        type_="check",
    )
    op.drop_column("payments", "admin_message_thread_id")
    op.drop_column("payments", "receipt_file_name")
    op.drop_column("payments", "receipt_mime_type")
    op.drop_column("payments", "receipt_file_size")
    op.drop_column("payments", "duration_months")
    op.drop_column("payments", "offer_code")
