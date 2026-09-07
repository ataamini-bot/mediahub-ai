"""add plan English names

Revision ID: c7e4d2a9f610
Revises: a3d8f2c6e910
Create Date: 2026-09-06 11:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7e4d2a9f610"
down_revision: Union[str, None] = "a3d8f2c6e910"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("name_en", sa.String(length=100), nullable=True),
    )
    op.execute(
        """
        UPDATE plans
        SET name_en = CASE
            WHEN slug = 'free' THEN 'Free'
            ELSE name
        END
        WHERE name_en IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("plans", "name_en")
