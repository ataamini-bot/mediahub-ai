"""add selected video frame rate to download jobs

Revision ID: a91e6d4b2c73
Revises: e6f7a8b9c0d1
Create Date: 2026-09-19 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a91e6d4b2c73"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column(
            "frame_rate",
            sa.Float(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "download_jobs",
        "frame_rate",
    )
