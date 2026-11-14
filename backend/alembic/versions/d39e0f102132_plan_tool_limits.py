"""Plan tool quotas, editable copy and independent single-portrait purchases.

Revision ID: d39e0f102132
Revises: c28d9e0f1021
"""
import json
from alembic import op
import sqlalchemy as sa

revision = "d39e0f102132"
down_revision = "c28d9e0f1021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plans", sa.Column("tool_limits", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.alter_column("payments", "plan_id", nullable=True)
    op.alter_column("payment_orders", "plan_id", nullable=True)
    op.drop_constraint("ck_payments_duration_days_positive", "payments", type_="check")
    op.create_check_constraint("ck_payments_duration_days_positive", "payments",
        "(plan_id IS NOT NULL AND duration_days > 0) OR (plan_id IS NULL AND duration_days = 0 AND offer_code = 'portrait-once')")
    op.create_table("portrait_credits",
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("media_tool_requests.id", ondelete="RESTRICT"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_portrait_credits_user_id", "portrait_credits", ["user_id"])
    for key, category, value in (("bot.tool_texts.fa", "bot_content", {}), ("bot.tool_texts.en", "bot_content", {}),
                                  ("portrait.price_irt", "payments", "0"), ("portrait.price_usdt", "payments", "0")):
        op.get_bind().execute(sa.text("""INSERT INTO application_settings
            (key, category, value_json, is_sensitive, description, version)
            VALUES (:key, :category, CAST(:value AS json), FALSE, :description, 1)
            ON CONFLICT (key) DO NOTHING"""),
            {"key": key, "category": category, "value": json.dumps(value), "description": key})


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM payment_orders WHERE plan_id IS NULL) OR EXISTS(SELECT 1 FROM payments WHERE plan_id IS NULL)")).scalar():
        raise RuntimeError("Single-portrait purchases exist; restore the pre-upgrade backup instead of discarding financial records")
    op.drop_table("portrait_credits")
    op.drop_constraint("ck_payments_duration_days_positive", "payments", type_="check")
    op.create_check_constraint("ck_payments_duration_days_positive", "payments", "duration_days > 0")
    op.alter_column("payments", "plan_id", nullable=False)
    op.alter_column("payment_orders", "plan_id", nullable=False)
    op.drop_column("plans", "tool_limits")
    op.execute("DELETE FROM application_settings WHERE key IN ('bot.tool_texts.fa','bot.tool_texts.en','portrait.price_irt','portrait.price_usdt')")
