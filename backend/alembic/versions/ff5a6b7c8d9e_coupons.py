"""Scoped coupons with durable capacity reservations and payment snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "ff5a6b7c8d9e"
down_revision = "fe4f5a6b7c8d"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column(key, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for key in ("created_at", "updated_at")]


def upgrade():
    op.create_table("coupons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(4), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("value", sa.Numeric(18, 4), nullable=False),
        sa.Column("max_discount", sa.Numeric(18, 4)),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("total_limit", sa.Integer()),
        sa.Column("per_user_limit", sa.Integer()),
        sa.Column("plan_ids", sa.JSON(), nullable=False),
        sa.Column("duration_days", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), *timestamps(),
        sa.UniqueConstraint("code", "currency", name="uq_coupon_code_currency"),
        sa.CheckConstraint("currency IN ('IRT', 'USDT')", name="ck_coupon_currency"),
        sa.CheckConstraint("kind IN ('percent', 'fixed')", name="ck_coupon_kind"),
        sa.CheckConstraint("value > 0 AND (kind != 'percent' OR value < 100)", name="ck_coupon_value"),
        sa.CheckConstraint("max_discount IS NULL OR max_discount > 0", name="ck_coupon_cap"),
        sa.CheckConstraint("total_limit IS NULL OR total_limit > 0", name="ck_coupon_total_limit"),
        sa.CheckConstraint("per_user_limit IS NULL OR per_user_limit > 0", name="ck_coupon_user_limit"),
        sa.CheckConstraint("starts_at IS NULL OR expires_at IS NULL OR expires_at > starts_at", name="ck_coupon_dates"),
    )
    op.create_table("coupon_uses",
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("payment_orders.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("coupon_id", sa.Uuid(), sa.ForeignKey("coupons.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT"), unique=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False), *timestamps(),
        sa.CheckConstraint("status IN ('reserved', 'redeemed', 'released')", name="ck_coupon_use_status"),
    )
    for field in ("coupon_id", "user_id"):
        op.create_index(f"ix_coupon_uses_{field}", "coupon_uses", [field])
    op.create_table("coupon_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("coupon_id", sa.Uuid(), sa.ForeignKey("coupons.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False), *timestamps(),
    )
    op.create_index("ix_coupon_actions_coupon_id", "coupon_actions", ["coupon_id"])
    op.add_column("payments", sa.Column("discount_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM coupon_uses")):
        raise RuntimeError("Cannot remove coupon order/payment history")
    op.drop_column("payments", "discount_snapshot")
    op.drop_table("coupon_uses")
    op.drop_table("coupon_actions")
    op.drop_table("coupons")
