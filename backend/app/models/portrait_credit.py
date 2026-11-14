"""One portrait entitlement per approved payment, separate from subscriptions."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import DateTime, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class PortraitCredit(Base):
    __tablename__ = "portrait_credits"
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("media_tool_requests.id", ondelete="RESTRICT"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
