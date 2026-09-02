from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "duration_days >= 0",
            name="ck_plans_duration_days_nonnegative",
        ),
        CheckConstraint("price >= 0", name="ck_plans_price_nonnegative"),
        CheckConstraint(
            "daily_download_limit IS NULL OR daily_download_limit > 0",
            name="ck_plans_daily_limit_positive",
        ),
        CheckConstraint(
            "max_file_size_mb IS NULL OR max_file_size_mb > 0",
            name="ck_plans_file_size_positive",
        ),
        CheckConstraint(
            "max_quality IS NULL OR max_quality > 0",
            name="ck_plans_quality_positive",
        ),
        CheckConstraint(
            "max_concurrent_downloads BETWEEN 1 AND 3",
            name="ck_plans_concurrency_range",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_plans_sort_order_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0"),
        nullable=False,
    )

    duration_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    daily_download_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    max_file_size_mb: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    max_quality: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    ai_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    priority_processing: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    is_unlimited: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    max_concurrent_downloads: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )

    forced_join_required: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
