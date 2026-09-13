"""Immutable checkout terms, stored before displaying payment instructions."""
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, JSON, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PaymentOrder(Base, TimestampMixin):
    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'submitted', 'cancelled')", name="ck_payment_orders_status"),
        CheckConstraint("currency IN ('IRT', 'USDT')", name="ck_payment_orders_currency"),
        Index("uq_payment_orders_user_open", "user_id", unique=True, postgresql_where=text("status = 'open'")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    payment_card_id: Mapped[int | None] = mapped_column(ForeignKey("payment_cards.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    currency: Mapped[str] = mapped_column(String(4))
    offer_snapshot: Mapped[dict] = mapped_column(JSON)
    destination_snapshot: Mapped[dict] = mapped_column(JSON)
    receipt_rules: Mapped[dict] = mapped_column(JSON)
