import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.db.base import (
    Base,
    TimestampMixin,
)


class DownloadJobStatus(
    str,
    enum.Enum,
):
    PENDING = "pending"
    PROCESSING = "processing"

    PAUSED = "paused"

    COMPLETED = "completed"
    FAILED = "failed"

    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DownloadJob(
    Base,
    TimestampMixin,
):
    __tablename__ = "download_jobs"

    # ========================================================
    # Primary key
    # ========================================================

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # ========================================================
    # User
    # ========================================================

    user_id: Mapped[
        Optional[int]
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )

    plan_id: Mapped[
        Optional[int]
    ] = mapped_column(
        ForeignKey(
            "plans.id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )

    plan_name_snapshot: Mapped[
        Optional[str]
    ] = mapped_column(
        String(100),
        nullable=True,
    )

    plan_limits_snapshot: Mapped[
        Optional[dict]
    ] = mapped_column(
        JSON,
        nullable=True,
    )

    # ========================================================
    # Download request
    # ========================================================

    source_url: Mapped[
        str
    ] = mapped_column(
        Text,
        nullable=False,
    )

    format_id: Mapped[
        Optional[str]
    ] = mapped_column(
        String(100),
        nullable=True,
    )

    quality: Mapped[
        Optional[str]
    ] = mapped_column(
        String(50),
        nullable=True,
    )

    media_type: Mapped[
        Optional[str]
    ] = mapped_column(
        String(50),
        nullable=True,
    )

    # --------------------------------------------------------
    # Multi-video / playlist item
    #
    # Examples:
    #
    # X post with 3 videos:
    #   1 -> first video
    #   2 -> second video
    #   3 -> third video
    #
    # Single-video posts keep this NULL.
    # --------------------------------------------------------

    playlist_index: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    # ========================================================
    # Status
    # ========================================================

    status: Mapped[
        DownloadJobStatus
    ] = mapped_column(
        Enum(
            DownloadJobStatus
        ),
        default=(
            DownloadJobStatus.PENDING
        ),
        index=True,
        nullable=False,
    )

    progress: Mapped[
        int
    ] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # ========================================================
    # Live download statistics
    # ========================================================

    downloaded_bytes: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        nullable=True,
    )

    total_bytes: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        nullable=True,
    )

    speed: Mapped[
        Optional[float]
    ] = mapped_column(
        Float,
        nullable=True,
    )

    eta: Mapped[
        Optional[int]
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    # ========================================================
    # Final file
    # ========================================================

    file_path: Mapped[
        Optional[str]
    ] = mapped_column(
        String(1024),
        nullable=True,
    )

    file_size: Mapped[
        Optional[int]
    ] = mapped_column(
        BigInteger,
        nullable=True,
    )

    error_message: Mapped[
        Optional[str]
    ] = mapped_column(
        Text,
        nullable=True,
    )

    # ========================================================
    # Celery
    # ========================================================

    celery_task_id: Mapped[
        Optional[str]
    ] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    # ========================================================
    # Timestamps
    # ========================================================

    started_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    completed_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    delivered_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
        index=True,
    )

    paused_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
        index=True,
    )

    cancelled_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    expired_at: Mapped[
        Optional[datetime]
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )
