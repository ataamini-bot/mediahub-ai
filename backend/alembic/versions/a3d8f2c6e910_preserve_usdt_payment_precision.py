"""preserve USDT payment precision

Revision ID: a3d8f2c6e910
Revises: 9b4e2d6f1a30
Create Date: 2026-09-05 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3d8f2c6e910"
down_revision: Union[str, None] = "9b4e2d6f1a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "payments",
        "amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Numeric(12, 4),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "payments",
        "amount",
        existing_type=sa.Numeric(12, 4),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
    )
