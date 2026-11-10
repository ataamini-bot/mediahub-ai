"""Internal credit, append-only money movements and a separate notification outbox."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "currency", name="uq_credit_account_currency"),
        CheckConstraint("currency IN ('IRT', 'USDT')", name="ck_credit_currency"),
        CheckConstraint("balance >= 0", name="ck_credit_nonnegative"),
        CheckConstraint("currency != 'IRT' OR balance = trunc(balance)", name="ck_credit_whole_toman"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    currency: Mapped[str] = mapped_column(String(4))
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    version: Mapped[int] = mapped_column(Integer, server_default="0")


class CreditEntry(Base):
    __tablename__ = "credit_entries"
    __table_args__ = (
        CheckConstraint("delta != 0", name="ck_credit_entry_nonzero"),
        CheckConstraint("balance_after >= 0", name="ck_credit_entry_balance"),
        CheckConstraint("kind IN ('adjustment', 'reversal', 'purchase')", name="ck_credit_entry_kind"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)  # The request's idempotency key.
    account_id: Mapped[int] = mapped_column(ForeignKey("credit_accounts.id", ondelete="RESTRICT"), index=True)
    delta: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    kind: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger)
    payload_hash: Mapped[str] = mapped_column(String(64))
    reverses_entry_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("credit_entries.id", ondelete="RESTRICT"), unique=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CreditNotice(Base):
    __tablename__ = "credit_notices"
    __table_args__ = (UniqueConstraint("entry_id", "kind", name="uq_credit_notice"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("credit_entries.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # user or report
    status: Mapped[str] = mapped_column(String(16), server_default="pending", index=True)
    claim_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
