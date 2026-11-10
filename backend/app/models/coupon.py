"""Coupon definitions and durable order reservations; monetary terms are snapshots."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Coupon(Base, TimestampMixin):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("code", "currency", name="uq_coupon_code_currency"),
        CheckConstraint("currency IN ('IRT', 'USDT')", name="ck_coupon_currency"),
        CheckConstraint("kind IN ('percent', 'fixed')", name="ck_coupon_kind"),
        CheckConstraint("value > 0 AND (kind != 'percent' OR value < 100)", name="ck_coupon_value"),
        CheckConstraint("max_discount IS NULL OR max_discount > 0", name="ck_coupon_cap"),
        CheckConstraint("total_limit IS NULL OR total_limit > 0", name="ck_coupon_total_limit"),
        CheckConstraint("per_user_limit IS NULL OR per_user_limit > 0", name="ck_coupon_user_limit"),
        CheckConstraint("starts_at IS NULL OR expires_at IS NULL OR expires_at > starts_at", name="ck_coupon_dates"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(8))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    max_discount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_limit: Mapped[int | None] = mapped_column(Integer)
    per_user_limit: Mapped[int | None] = mapped_column(Integer)
    plan_ids: Mapped[list] = mapped_column(JSON, default=list)
    duration_days: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CouponUse(Base, TimestampMixin):
    __tablename__ = "coupon_uses"
    __table_args__ = (CheckConstraint("status IN ('reserved', 'redeemed', 'released')", name="ck_coupon_use_status"),)
    # One coupon per order, regardless of future changes to its code or terms.
    order_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("payment_orders.id", ondelete="RESTRICT"), primary_key=True)
    coupon_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("coupons.id", ondelete="RESTRICT"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="reserved")
    snapshot: Mapped[dict] = mapped_column(JSON)


class CouponAction(Base, TimestampMixin):
    __tablename__ = "coupon_actions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    coupon_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("coupons.id", ondelete="RESTRICT"), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
