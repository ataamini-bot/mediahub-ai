"""Durable broadcast drafts and per-recipient delivery state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "fd3e4f5a6b7c"
down_revision = "fc2d3e4f5a6b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=False), nullable=False, unique=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("confirmation_token", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("next_send_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(60)),
    )
    op.create_index("ix_broadcasts_status", "broadcasts", ["status"])
    op.create_table("broadcast_recipients",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("broadcast_id", sa.Integer(), sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", postgresql.UUID(as_uuid=False)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("error_code", sa.String(60)),
        sa.UniqueConstraint("broadcast_id", "telegram_id", name="uq_broadcast_recipient"),
    )
    op.create_index("ix_broadcast_recipients_broadcast_id", "broadcast_recipients", ["broadcast_id"])
    op.create_index("ix_broadcast_recipients_status", "broadcast_recipients", ["status"])


def downgrade():
    op.drop_table("broadcast_recipients")
    op.drop_table("broadcasts")
