"""Bounded downloads and reusable public Telegram outputs.

Revision ID: e40f10213243
Revises: d39e0f102132
"""
from alembic import op
import sqlalchemy as sa

revision = "e40f10213243"
down_revision = "d39e0f102132"
branch_labels = depends_on = None


def upgrade():
    op.add_column("media_tool_requests", sa.Column("link_cache_key", sa.String(64), nullable=True))
    op.add_column("download_jobs", sa.Column("execution_token", sa.String(255), nullable=True))
    op.add_column("download_jobs", sa.Column("cache_public", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("download_jobs", sa.Column("public_output_key", sa.String(64), nullable=True))
    op.add_column("download_jobs", sa.Column("telegram_file_id", sa.String(1024), nullable=True))
    op.add_column("download_jobs", sa.Column("telegram_file_kind", sa.String(16), nullable=True))
    op.add_column("download_jobs", sa.Column("telegram_filename", sa.String(255), nullable=True))
    op.add_column("download_jobs", sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("download_jobs", sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("download_jobs", sa.Column("delivery_kind", sa.String(16), nullable=False, server_default="document"))
    op.create_index("ix_download_jobs_status_created_at", "download_jobs", ["status", "created_at"])
    op.create_table("public_outputs",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("leader_job_id", sa.Integer, sa.ForeignKey("download_jobs.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_id", sa.String(1024)), sa.Column("file_kind", sa.String(16)),
        sa.Column("filename", sa.String(255)), sa.Column("file_size", sa.BigInteger))
    op.create_index("ix_public_outputs_expires_at", "public_outputs", ["expires_at"])


def downgrade():
    op.drop_column("media_tool_requests", "link_cache_key")
    op.drop_table("public_outputs")
    op.drop_index("ix_download_jobs_status_created_at", "download_jobs")
    for column in ("execution_token", "cache_public", "public_output_key", "telegram_file_id", "telegram_file_kind", "telegram_filename",
                   "delivery_started_at", "enqueued_at", "delivery_kind"):
        op.drop_column("download_jobs", column)
