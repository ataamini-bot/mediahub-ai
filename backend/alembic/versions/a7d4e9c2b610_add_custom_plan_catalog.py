"""add custom plan catalog

Revision ID: a7d4e9c2b610
Revises: 4f8b1d3e6a20
Create Date: 2026-09-01 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d4e9c2b610"
down_revision: Union[str, None] = "4f8b1d3e6a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "max_concurrent_downloads",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "plans",
        sa.Column(
            "forced_join_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "plans",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "plans",
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "plans",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_plans_duration_days_nonnegative",
        "plans",
        "duration_days >= 0",
    )
    op.create_check_constraint(
        "ck_plans_price_nonnegative",
        "plans",
        "price >= 0",
    )
    op.create_check_constraint(
        "ck_plans_daily_limit_positive",
        "plans",
        "daily_download_limit IS NULL OR daily_download_limit > 0",
    )
    op.create_check_constraint(
        "ck_plans_file_size_positive",
        "plans",
        "max_file_size_mb IS NULL OR max_file_size_mb > 0",
    )
    op.create_check_constraint(
        "ck_plans_quality_positive",
        "plans",
        "max_quality IS NULL OR max_quality > 0",
    )
    op.create_check_constraint(
        "ck_plans_concurrency_range",
        "plans",
        "max_concurrent_downloads BETWEEN 1 AND 3",
    )
    op.create_check_constraint(
        "ck_plans_sort_order_nonnegative",
        "plans",
        "sort_order >= 0",
    )

    # Free is the only built-in catalog entry. Its limitations remain editable.
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
                max_concurrent_downloads,
                forced_join_required,
                sort_order,
                is_system,
                is_active,
                deleted_at
            )
            VALUES (
                'Free',
                'free',
                'پلن رایگان پیش‌فرض',
                0,
                0,
                3,
                300,
                720,
                FALSE,
                FALSE,
                FALSE,
                1,
                TRUE,
                0,
                TRUE,
                TRUE,
                NULL
            )
            ON CONFLICT (slug) DO UPDATE
            SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                price = EXCLUDED.price,
                duration_days = EXCLUDED.duration_days,
                daily_download_limit = EXCLUDED.daily_download_limit,
                max_file_size_mb = EXCLUDED.max_file_size_mb,
                max_quality = EXCLUDED.max_quality,
                ai_enabled = EXCLUDED.ai_enabled,
                priority_processing = EXCLUDED.priority_processing,
                is_unlimited = EXCLUDED.is_unlimited,
                max_concurrent_downloads = EXCLUDED.max_concurrent_downloads,
                forced_join_required = EXCLUDED.forced_join_required,
                sort_order = EXCLUDED.sort_order,
                is_system = EXCLUDED.is_system,
                is_active = EXCLUDED.is_active,
                deleted_at = EXCLUDED.deleted_at,
                updated_at = now()
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET
                is_active = FALSE,
                deleted_at = COALESCE(deleted_at, now()),
                updated_at = now()
            WHERE slug <> 'free'
            """
        )
    )

    op.add_column(
        "payments",
        sa.Column("duration_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("plan_name_snapshot", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column(
            "plan_limits_snapshot",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.alter_column(
        "payments",
        "offer_code",
        existing_type=sa.String(length=32),
        type_=sa.String(length=100),
        existing_nullable=False,
    )
    op.alter_column(
        "payments",
        "duration_months",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.execute(
        sa.text(
            """
            UPDATE payments AS payment
            SET
                duration_days = CASE payment.duration_months
                    WHEN 12 THEN 365
                    WHEN 6 THEN 180
                    WHEN 3 THEN 90
                    ELSE 30
                END,
                plan_name_snapshot = plan.name,
                plan_limits_snapshot = json_build_object(
                    'daily_download_limit', plan.daily_download_limit,
                    'max_file_size_mb', plan.max_file_size_mb,
                    'max_quality', plan.max_quality,
                    'max_concurrent_downloads', plan.max_concurrent_downloads,
                    'priority_processing', plan.priority_processing,
                    'forced_join_required', plan.forced_join_required
                )
            FROM plans AS plan
            WHERE payment.plan_id = plan.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET
                duration_days = COALESCE(duration_days, 30),
                plan_name_snapshot = COALESCE(plan_name_snapshot, 'Legacy plan')
            WHERE duration_days IS NULL OR plan_name_snapshot IS NULL
            """
        )
    )
    op.alter_column("payments", "duration_days", nullable=False)
    op.alter_column("payments", "plan_name_snapshot", nullable=False)
    op.create_check_constraint(
        "ck_payments_duration_days_positive",
        "payments",
        "duration_days > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payments_duration_days_positive",
        "payments",
        type_="check",
    )
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET duration_months = CASE
                WHEN duration_days >= 330 THEN 12
                WHEN duration_days >= 150 THEN 6
                WHEN duration_days >= 60 THEN 3
                ELSE 1
            END
            WHERE duration_months IS NULL
            """
        )
    )
    op.alter_column(
        "payments",
        "duration_months",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.execute(sa.text("UPDATE payments SET offer_code = left(offer_code, 32)"))
    op.alter_column(
        "payments",
        "offer_code",
        existing_type=sa.String(length=100),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_column("payments", "plan_limits_snapshot")
    op.drop_column("payments", "plan_name_snapshot")
    op.drop_column("payments", "duration_days")

    op.execute(
        sa.text(
            """
            UPDATE plans
            SET
                is_active = TRUE,
                deleted_at = NULL,
                updated_at = now()
            WHERE slug IN ('silver', 'gold', 'premium')
            """
        )
    )
    op.drop_constraint("ck_plans_sort_order_nonnegative", "plans", type_="check")
    op.drop_constraint("ck_plans_concurrency_range", "plans", type_="check")
    op.drop_constraint("ck_plans_quality_positive", "plans", type_="check")
    op.drop_constraint("ck_plans_file_size_positive", "plans", type_="check")
    op.drop_constraint("ck_plans_daily_limit_positive", "plans", type_="check")
    op.drop_constraint("ck_plans_price_nonnegative", "plans", type_="check")
    op.drop_constraint("ck_plans_duration_days_nonnegative", "plans", type_="check")
    op.drop_column("plans", "deleted_at")
    op.drop_column("plans", "is_system")
    op.drop_column("plans", "sort_order")
    op.drop_column("plans", "forced_join_required")
    op.drop_column("plans", "max_concurrent_downloads")
