"""Cover documents are sent directly by the bot, outside the quota/worker queue."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CoverRequest(Base):
    __tablename__ = "cover_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "callback_id", name="uq_cover_request_callback"),
        Index("ix_cover_requests_user_created", "user_id", "created_at", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    callback_id: Mapped[str] = mapped_column(String(128))
    source_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
