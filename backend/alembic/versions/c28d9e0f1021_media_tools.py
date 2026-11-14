"""Durable music recognition and portrait requests.

Revision ID: c28d9e0f1021
Revises: b17c8d9e0f10
"""
from alembic import op
import sqlalchemy as sa

revision = "c28d9e0f1021"
down_revision = "b17c8d9e0f10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("media_tool_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error_code", sa.String(48)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("files_removed_at", sa.DateTime(timezone=True)),
        sa.Column("download_state", sa.String(16)),
        sa.Column("delivery_state", sa.String(16)),
        sa.Column("download_job_id", sa.Integer()),
        sa.CheckConstraint("kind IN ('music','portrait')", name="ck_media_tool_kind"),
        sa.CheckConstraint("status IN ('pending','processing','completed','failed','cancelled','uncertain','expired')", name="ck_media_tool_status"))
    op.create_index("ix_media_tool_user_created", "media_tool_requests", ["user_id", "created_at"])
    op.create_index("ix_media_tool_kind_created", "media_tool_requests", ["kind", "created_at"])


def downgrade():
    op.drop_table("media_tool_requests")
