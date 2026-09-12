import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    SCHEDULED = "scheduled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    plan_id: Mapped[int] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus),
        default=SubscriptionStatus.ACTIVE,
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # The effective daily quota is snapshotted on the subscription.  Each
    # approved renewal adds the newly purchased quota, so extending a finite
    # plan also preserves the quota units the customer paid for.
    daily_download_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    auto_renew: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )
