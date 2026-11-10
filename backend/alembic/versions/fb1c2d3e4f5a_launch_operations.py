"""Customer support actions, quota reset boundaries and backup settings.

Revision ID: fb1c2d3e4f5a
Revises: fa0b1c2d3e4f
"""
from alembic import op
import sqlalchemy as sa

revision = "fb1c2d3e4f5a"
down_revision = "fa0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("quota_reset_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("conversion_quota_reset_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("customer_actions",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_customer_actions_user_id", "customer_actions", ["user_id"])
    for key, value in (("backup.enabled", "true"), ("backup.hour", "3"),
                       ("backup.daily", "7"), ("backup.weekly", "4"), ("backup.monthly", "6")):
        op.get_bind().execute(sa.text("""INSERT INTO application_settings
            (key, category, value_json, is_sensitive, description, version)
            VALUES (:key, 'backups', CAST(:value AS json), false, 'Backup schedule/retention', 1)
            ON CONFLICT (key) DO NOTHING"""), {"key": key, "value": value})


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM customer_actions)")).scalar_one():
        raise RuntimeError("Customer audit history exists; use a forward recovery migration.")
    op.execute("DELETE FROM application_settings WHERE category='backups' AND key LIKE 'backup.%'")
    op.drop_table("customer_actions")
    op.drop_column("users", "conversion_quota_reset_at")
    op.drop_column("users", "quota_reset_at")
