"""add download progress statistics

Revision ID: 7ec0cb2b7cfd
Revises: f02e234c39a8
Create Date: 2026-08-25 13:00:53.756057

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7ec0cb2b7cfd"
down_revision: Union[str, None] = "f02e234c39a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column(
            "downloaded_bytes",
            sa.BigInteger(),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "total_bytes",
            sa.BigInteger(),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "speed",
            sa.Float(),
            nullable=True,
        ),
    )

    op.add_column(
        "download_jobs",
        sa.Column(
            "eta",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.alter_column(
        "download_jobs",
        "file_size",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "download_jobs",
        "file_size",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )

    op.drop_column(
        "download_jobs",
        "eta",
    )

    op.drop_column(
        "download_jobs",
        "speed",
    )

    op.drop_column(
        "download_jobs",
        "total_bytes",
    )

    op.drop_column(
        "download_jobs",
        "downloaded_bytes",
    )
