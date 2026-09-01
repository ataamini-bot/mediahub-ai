from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ApplicationSetting(Base, TimestampMixin):
    __tablename__ = "application_settings"
    __table_args__ = (
        CheckConstraint(
            "NOT (value_json IS NOT NULL AND encrypted_value IS NOT NULL)",
            name="ck_application_settings_single_value",
        ),
        CheckConstraint(
            "encrypted_value IS NULL OR is_sensitive = TRUE",
            name="ck_application_settings_encrypted_sensitive",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    key: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        index=True,
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(80),
        index=True,
        nullable=False,
    )

    value_json: Mapped[Any | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )

    encrypted_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_sensitive: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )

    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
