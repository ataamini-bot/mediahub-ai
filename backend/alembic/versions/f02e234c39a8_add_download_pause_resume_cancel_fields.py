"""add download pause resume cancel fields

Revision ID: f02e234c39a8
Revises: 3086ce90607c
Create Date: 2026-08-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f02e234c39a8"
down_revision: Union[str, None] = "3086ce90607c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --------------------------------------------------------
    # Extend PostgreSQL enum
    # --------------------------------------------------------

    op.execute(
        "ALTER TYPE downloadjobstatus "
        "ADD VALUE IF NOT EXISTS 'PAUSED'"
    )

    op.execute(
        "ALTER TYPE downloadjobstatus "
        "ADD VALUE IF NOT EXISTS 'CANCELLED'"
    )

    op.execute(
        "ALTER TYPE downloadjobstatus "
        "ADD VALUE IF NOT EXISTS 'EXPIRED'"
    )

    # --------------------------------------------------------
    # Add columns
    # --------------------------------------------------------

    op.add_column(
        "download_jobs",
        sa.Column(
            "celery_task_id",
            sa.String(length=255),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "paused_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "expired_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # --------------------------------------------------------
    # Indexes
    # --------------------------------------------------------

    op.create_index(
        "ix_download_jobs_celery_task_id",
        "download_jobs",
        [
            "celery_task_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_download_jobs_paused_at",
        "download_jobs",
        [
            "paused_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    # --------------------------------------------------------
    # Drop indexes
    # --------------------------------------------------------

    op.drop_index(
        "ix_download_jobs_paused_at",
        table_name="download_jobs",
    )

    op.drop_index(
        "ix_download_jobs_celery_task_id",
        table_name="download_jobs",
    )

    # --------------------------------------------------------
    # Drop columns
    # --------------------------------------------------------

    op.drop_column(
        "download_jobs",
        "expired_at",
    )

    op.drop_column(
        "download_jobs",
        "cancelled_at",
    )

    op.drop_column(
        "download_jobs",
        "paused_at",
    )

    op.drop_column(
        "download_jobs",
        "celery_task_id",
    )

    # PostgreSQL enum values are intentionally
    # kept during downgrade.
