"""Reusable Telegram references, never media bytes or uploaded user files."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PublicOutput(Base):
    __tablename__ = "public_outputs"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    leader_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_jobs.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    file_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
