"""enforce download plan limits

Revision ID: e1f6b8c3d920
Revises: a7d4e9c2b610
Create Date: 2026-09-02 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f6b8c3d920"
down_revision: Union[str, None] = "a7d4e9c2b610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column("plan_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "download_jobs",
        sa.Column("plan_name_snapshot", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "download_jobs",
        sa.Column("plan_limits_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "download_jobs",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_download_jobs_plan_id_plans",
        "download_jobs",
        "plans",
        ["plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_download_jobs_plan_id",
        "download_jobs",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        "ix_download_jobs_delivered_at",
        "download_jobs",
        ["delivered_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_download_jobs_delivered_at", table_name="download_jobs")
    op.drop_index("ix_download_jobs_plan_id", table_name="download_jobs")
    op.drop_constraint(
        "fk_download_jobs_plan_id_plans",
        "download_jobs",
        type_="foreignkey",
    )
    op.drop_column("download_jobs", "delivered_at")
    op.drop_column("download_jobs", "plan_limits_snapshot")
    op.drop_column("download_jobs", "plan_name_snapshot")
    op.drop_column("download_jobs", "plan_id")
