"""Paid tool attempts have a durable identity; media itself is temporary."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MediaToolRequest(Base):
    __tablename__ = "media_tool_requests"
    __table_args__ = (
        CheckConstraint("kind IN ('music','portrait')", name="ck_media_tool_kind"),
        CheckConstraint("status IN ('pending','processing','completed','failed','cancelled','uncertain','expired')", name="ck_media_tool_status"),
        Index("ix_media_tool_user_created", "user_id", "created_at"),
        Index("ix_media_tool_kind_created", "kind", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    files_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_state: Mapped[str | None] = mapped_column(String(16))
    delivery_state: Mapped[str | None] = mapped_column(String(16))
    downloads: Mapped[dict] = mapped_column(JSON, default=dict)
    download_job_id: Mapped[int | None] = mapped_column(Integer)
