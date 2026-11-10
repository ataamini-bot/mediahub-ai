from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PaymentCard(Base, TimestampMixin):
    __tablename__ = "payment_cards"
    __table_args__ = (
        Index(
            "ix_payment_cards_active_order",
            "is_active",
            "sort_order",
            "id",
        ),
        CheckConstraint(
            "card_number ~ '^[0-9]{16}$'",
            name="ck_payment_cards_number_digits",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_payment_cards_sort_order_nonnegative",
        ),
        CheckConstraint(
            "selection_count >= 0",
            name="ck_payment_cards_selection_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    card_number: Mapped[str] = mapped_column(
        String(16),
        unique=True,
        nullable=False,
    )
    card_holder: Mapped[str] = mapped_column(String(120), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    selection_count: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
        nullable=False,
    )
    last_selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class UsdtDestination(Base, TimestampMixin):
    __tablename__ = "usdt_destinations"
    __table_args__ = (
        Index(
            "ix_usdt_destinations_active_order",
            "is_active",
            "sort_order",
            "id",
        ),
        UniqueConstraint(
            "network_code",
            "address",
            name="uq_usdt_destinations_network_address",
        ),
        CheckConstraint(
            "confirmations_required BETWEEN 1 AND 1000",
            name="ck_usdt_destinations_confirmations_range",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_usdt_destinations_sort_order_nonnegative",
        ),
        CheckConstraint(
            "selection_count >= 0",
            name="ck_usdt_destinations_selection_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    network_name: Mapped[str] = mapped_column(String(100), nullable=False)
    network_code: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_symbol: Mapped[str] = mapped_column(
        String(20),
        default="USDT",
        server_default="USDT",
        nullable=False,
    )
    contract_address: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    explorer_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    confirmations_required: Mapped[int] = mapped_column(
        Integer,
        default=20,
        server_default="20",
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    selection_count: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
        nullable=False,
    )
    last_selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
