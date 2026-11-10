from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_coupon_code(value):
    code = str(value or "").strip().upper()
    code = code.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,31}", code):
        raise ValueError("coupon_code_invalid")
    return code


class CouponTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    currency: Literal["IRT", "USDT"]
    kind: Literal["percent", "fixed"]
    value: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    max_discount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    total_limit: int | None = Field(default=None, gt=0, le=1000000000)
    per_user_limit: int | None = Field(default=1, gt=0, le=1000000000)
    plan_ids: list[int] = Field(default_factory=list, max_length=100)
    duration_days: list[int] = Field(default_factory=list, max_length=100)
    active: bool = True

    _code = field_validator("code", mode="before")(normalize_coupon_code)

    @field_validator("value", "max_discount", mode="before")
    @classmethod
    def exact_money(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Use a decimal string for amounts")
        return value

    @field_validator("starts_at", "expires_at")
    @classmethod
    def aware_date(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Specify a timezone")
            value = value.astimezone(timezone.utc)
        return value

    @model_validator(mode="after")
    def constraints(self):
        if self.kind == "percent" and self.value >= 100:
            raise ValueError("Percentage must be less than 100; zero-price checkouts are not supported")
        if self.currency == "IRT" and ((self.kind == "fixed" and self.value != self.value.to_integral_value())
                or (self.max_discount is not None and self.max_discount != self.max_discount.to_integral_value())):
            raise ValueError("Toman amounts must be whole numbers")
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("Expiry must follow the start time")
        if any(i <= 0 for i in self.plan_ids) or any(i < 1 or i > 3650 for i in self.duration_days):
            raise ValueError("Invalid plan or duration scope")
        self.plan_ids = sorted(set(self.plan_ids))
        self.duration_days = sorted(set(self.duration_days))
        return self


class CouponChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_telegram_id: int = Field(gt=0)
    request_id: UUID
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=500)
    terms: CouponTerms

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return str(value).strip()


class CouponQuote(BaseModel):
    telegram_id: int = Field(gt=0)
    offer_code: str = Field(min_length=3, max_length=100)
    currency: Literal["IRT", "USDT"]
    coupon_code: str
    _code = field_validator("coupon_code", mode="before")(normalize_coupon_code)
