from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class HomeButton(Base, TimestampMixin):
    __tablename__ = "home_buttons"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('url', 'message', 'buy', 'subscription', "
            "'support', 'tutorial', 'faq')",
            name="ck_home_buttons_action_type",
        ),
        CheckConstraint(
            "style IN ('default', 'primary', 'success', 'danger')",
            name="ck_home_buttons_style",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_home_buttons_sort_order_nonnegative",
        ),
        Index("ix_home_buttons_active_order", "is_active", "sort_order", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label_fa: Mapped[str] = mapped_column(String(64), nullable=False)
    label_en: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(24), nullable=False)
    action_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    style: Mapped[str] = mapped_column(
        String(16), default="default", server_default="default", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class RequiredChannel(Base, TimestampMixin):
    __tablename__ = "required_channels"
    __table_args__ = (
        CheckConstraint(
            "sort_order >= 0",
            name="ck_required_channels_sort_order_nonnegative",
        ),
        Index(
            "ix_required_channels_active_order",
            "is_active",
            "sort_order",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    invite_url: Mapped[str] = mapped_column(String(500), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SupportTicket(Base, TimestampMixin):
    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint(
            "category IN ('financial', 'technical', 'account', 'general')",
            name="ck_support_tickets_category",
        ),
        CheckConstraint(
            "status IN ('open', 'answered', 'closed')",
            name="ck_support_tickets_status",
        ),
        Index("ix_support_tickets_status_updated", "status", "updated_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open", nullable=False
    )
    assigned_admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_kind IN ('user', 'admin')",
            name="ck_support_messages_sender_kind",
        ),
        CheckConstraint(
            "file_type IS NULL OR file_type IN ('photo', 'document', 'video', 'voice')",
            name="ck_support_messages_file_type",
        ),
        Index("ix_support_messages_ticket_created", "ticket_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    sender_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sender_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

