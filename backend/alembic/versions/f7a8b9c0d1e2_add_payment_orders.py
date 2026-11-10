"""Save checkout terms before payment instructions are displayed.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_card_id", sa.Integer(), sa.ForeignKey("payment_cards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column("currency", sa.String(4), nullable=False),
        sa.Column("offer_snapshot", sa.JSON(), nullable=False),
        sa.Column("destination_snapshot", sa.JSON(), nullable=False),
        sa.Column("receipt_rules", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('open', 'submitted', 'cancelled')", name="ck_payment_orders_status"),
        sa.CheckConstraint("currency IN ('IRT', 'USDT')", name="ck_payment_orders_currency"),
    )
    op.create_index("ix_payment_orders_user_id", "payment_orders", ["user_id"])
    op.create_index("uq_payment_orders_user_open", "payment_orders", ["user_id"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))
    op.add_column("payments", sa.Column("order_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_payments_order_id", "payments", "payment_orders", ["order_id"], ["id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_payments_order_id", "payments", ["order_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM payment_orders)")).scalar_one():
        raise RuntimeError("Payment orders must be preserved. Use forward recovery.")
    op.drop_constraint("uq_payments_order_id", "payments", type_="unique")
    op.drop_constraint("fk_payments_order_id", "payments", type_="foreignkey")
    op.drop_column("payments", "order_id")
    op.drop_table("payment_orders")
