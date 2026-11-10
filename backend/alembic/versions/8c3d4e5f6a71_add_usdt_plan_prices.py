"""add optional USDT prices to plans"""

from alembic import op
import sqlalchemy as sa

revision = "8c3d4e5f6a71"
down_revision = "7a2c9e1f4b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("price_usdt", sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "price_usdt")
