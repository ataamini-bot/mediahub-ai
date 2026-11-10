"""make plan limits nullable and update tiers

Revision ID: b693a1fe01f0
Revises: d87710fec995
Create Date: 2026-08-30 21:01:57.239343

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b693a1fe01f0'
down_revision: Union[str, None] = 'd87710fec995'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "plans",
        "daily_download_limit",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "plans",
        "max_file_size_mb",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "plans",
        "max_quality",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # The new nullable limits replace the ambiguous
    # all-or-nothing is_unlimited flag.
    op.drop_column(
        "plans",
        "is_unlimited",
    )

    # Preserve IDs referenced by existing subscriptions.
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET slug = 'silver'
            WHERE slug = 'professional'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET slug = 'gold'
            WHERE slug = 'commercial'
            """
        )
    )

    # Required application plans.
    op.execute(
        sa.text(
            """
            INSERT INTO plans (
                name,
                slug,
                price,
                duration_days,
                daily_download_limit,
                max_file_size_mb,
                max_quality,
                ai_enabled,
                priority_processing,
                is_active
            )
            VALUES (
                'Free',
                'free',
                0,
                30,
                3,
                300,
                720,
                FALSE,
                FALSE,
                TRUE
            )
            ON CONFLICT (slug) DO UPDATE
            SET
                name = EXCLUDED.name,
                price = EXCLUDED.price,
                duration_days = EXCLUDED.duration_days,
                daily_download_limit = EXCLUDED.daily_download_limit,
                max_file_size_mb = EXCLUDED.max_file_size_mb,
                max_quality = EXCLUDED.max_quality,
                ai_enabled = EXCLUDED.ai_enabled,
                priority_processing = EXCLUDED.priority_processing,
                is_active = EXCLUDED.is_active,
                updated_at = now()
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO plans (
                name,
                slug,
                price,
                duration_days,
                daily_download_limit,
                max_file_size_mb,
                max_quality,
                ai_enabled,
                priority_processing,
                is_active
            )
            VALUES (
                'Silver',
                'silver',
                79000,
                30,
                50,
                NULL,
                NULL,
                TRUE,
                FALSE,
                TRUE
            )
            ON CONFLICT (slug) DO UPDATE
            SET
                name = EXCLUDED.name,
                price = EXCLUDED.price,
                duration_days = EXCLUDED.duration_days,
                daily_download_limit = EXCLUDED.daily_download_limit,
                max_file_size_mb = EXCLUDED.max_file_size_mb,
                max_quality = EXCLUDED.max_quality,
                ai_enabled = EXCLUDED.ai_enabled,
                priority_processing = EXCLUDED.priority_processing,
                is_active = EXCLUDED.is_active,
                updated_at = now()
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO plans (
                name,
                slug,
                price,
                duration_days,
                daily_download_limit,
                max_file_size_mb,
                max_quality,
                ai_enabled,
                priority_processing,
                is_active
            )
            VALUES (
                'Gold',
                'gold',
                99000,
                30,
                NULL,
                NULL,
                NULL,
                TRUE,
                TRUE,
                TRUE
            )
            ON CONFLICT (slug) DO UPDATE
            SET
                name = EXCLUDED.name,
                price = EXCLUDED.price,
                duration_days = EXCLUDED.duration_days,
                daily_download_limit = EXCLUDED.daily_download_limit,
                max_file_size_mb = EXCLUDED.max_file_size_mb,
                max_quality = EXCLUDED.max_quality,
                ai_enabled = EXCLUDED.ai_enabled,
                priority_processing = EXCLUDED.priority_processing,
                is_active = EXCLUDED.is_active,
                updated_at = now()
            """
        )
    )


def downgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "is_unlimited",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )

    # Restore the previous plan definitions.
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET
                name = 'Free',
                price = 0,
                duration_days = 30,
                daily_download_limit = 10,
                max_file_size_mb = 100,
                max_quality = 720,
                ai_enabled = FALSE,
                priority_processing = FALSE,
                is_unlimited = FALSE,
                is_active = TRUE,
                updated_at = now()
            WHERE slug = 'free'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET
                name = 'Professional',
                slug = 'professional',
                price = 499000,
                duration_days = 30,
                daily_download_limit = 100,
                max_file_size_mb = 500,
                max_quality = 1080,
                ai_enabled = TRUE,
                priority_processing = FALSE,
                is_unlimited = FALSE,
                is_active = TRUE,
                updated_at = now()
            WHERE slug = 'silver'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET
                name = 'Commercial',
                slug = 'commercial',
                price = 999000,
                duration_days = 30,
                daily_download_limit = 500,
                max_file_size_mb = 2000,
                max_quality = 2160,
                ai_enabled = TRUE,
                priority_processing = TRUE,
                is_unlimited = FALSE,
                is_active = TRUE,
                updated_at = now()
            WHERE slug = 'gold'
            """
        )
    )

    # Make any custom unlimited plans compatible with the old schema.
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET daily_download_limit = 500
            WHERE daily_download_limit IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET max_file_size_mb = 2000
            WHERE max_file_size_mb IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET max_quality = 2160
            WHERE max_quality IS NULL
            """
        )
    )

    op.alter_column(
        "plans",
        "max_quality",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "plans",
        "max_file_size_mb",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "plans",
        "daily_download_limit",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "plans",
        "is_unlimited",
        existing_type=sa.Boolean(),
        server_default=None,
    )
