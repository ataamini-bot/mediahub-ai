import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )

    plan_id: Mapped[int] = mapped_column(
        ForeignKey(
            "plans.id",
            ondelete="RESTRICT",
        ),
        index=True,
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus),
        default=PaymentStatus.PENDING,
        index=True,
        nullable=False,
    )

    receipt_file_id: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    receipt_file_unique_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    receipt_file_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    user_receipt_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    admin_chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    admin_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    reviewed_by_telegram_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejection_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "subscriptions.id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )
