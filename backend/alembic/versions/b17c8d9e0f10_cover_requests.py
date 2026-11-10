"""Journal direct cover downloads without adding to customer quotas."""
from alembic import op
import sqlalchemy as sa

revision = "b17c8d9e0f10"
down_revision = "a06b7c8d9e0f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cover_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("callback_id", sa.String(128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "callback_id", name="uq_cover_request_callback"))
    op.create_index("ix_cover_requests_user_created", "cover_requests", ["user_id", "created_at", "id"])


def downgrade():
    op.drop_table("cover_requests")
