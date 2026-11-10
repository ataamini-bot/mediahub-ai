"""widen user Telegram identifiers

Revision ID: 4f8b1d3e6a20
Revises: c8f4d1a2b7e9
Create Date: 2026-09-01 08:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4f8b1d3e6a20"
down_revision: Union[str, None] = "c8f4d1a2b7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Telegram identifiers have exceeded the signed 32-bit integer range.
    # The ORM already uses BigInteger, so align the existing PostgreSQL
    # column with the runtime model before admin assignment relies on it.
    op.alter_column(
        "users",
        "telegram_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "telegram_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="telegram_id::integer",
    )
