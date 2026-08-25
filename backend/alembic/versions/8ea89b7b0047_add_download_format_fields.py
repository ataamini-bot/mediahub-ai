"""add download format fields

Revision ID: 8ea89b7b0047
Revises: 6d7c5026ba69
Create Date: 2026-08-09 14:08:59.488448

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8ea89b7b0047"
down_revision: Union[str, None] = "6d7c5026ba69"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column(
            "format_id",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "quality",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "media_type",
            sa.String(length=50),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "download_jobs",
        "media_type",
    )

    op.drop_column(
        "download_jobs",
        "quality",
    )

    op.drop_column(
        "download_jobs",
        "format_id",
    )
