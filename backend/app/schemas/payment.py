from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.payment import PaymentStatus


class PaymentOfferResponse(BaseModel):
    code: str
    label: str
    duration_days: int = Field(gt=0)
    price: Decimal
    currency: Literal["IRT"] = "IRT"
    daily_download_limit: int | None
    max_file_size_mb: int | None
    max_quality: int | None
    max_concurrent_downloads: int
    priority_processing: bool
    forced_join_required: bool


class PaymentDestinationResponse(BaseModel):
    card_number: str
    card_holder: str
    bank_name: str | None = None


class PaymentReceiptRulesResponse(BaseModel):
    max_size_mb: int
    allowed_types: list[str]


class PaymentConfigurationResponse(BaseModel):
    offers: list[PaymentOfferResponse]
    destination: PaymentDestinationResponse
    receipt: PaymentReceiptRulesResponse


class PaymentCreate(BaseModel):
    telegram_id: int = Field(gt=0)
    offer_code: str = Field(min_length=3, max_length=100)
    receipt_file_id: str = Field(min_length=1, max_length=512)
    receipt_file_unique_id: str | None = Field(
        default=None,
        max_length=255,
    )
    receipt_file_type: Literal["photo", "document"]
    receipt_file_size: int | None = Field(default=None, ge=0)
    receipt_mime_type: str | None = Field(default=None, max_length=128)
    receipt_file_name: str | None = Field(default=None, max_length=255)
    user_receipt_message_id: int | None = Field(default=None, gt=0)

    @field_validator("offer_code")
    @classmethod
    def normalize_offer_code(cls, value: str) -> str:
        return value.strip().lower()


class PaymentAdminMessageUpdate(BaseModel):
    admin_chat_id: int
    admin_message_id: int = Field(gt=0)
    admin_message_thread_id: int | None = Field(default=None, gt=0)


class PaymentAdminReview(BaseModel):
    admin_telegram_id: int = Field(gt=0)


class PaymentReject(PaymentAdminReview):
    reason: str = Field(min_length=2, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return value.strip()


class PaymentResponse(BaseModel):
    id: int
    user_id: int
    plan_id: int
    amount: Decimal
    offer_code: str
    duration_months: Literal[1, 3, 6, 12] | None
    duration_days: int
    plan_name_snapshot: str
    plan_limits_snapshot: dict[str, Any]
    status: PaymentStatus
    receipt_file_id: str
    receipt_file_unique_id: str | None
    receipt_file_type: str
    receipt_file_size: int | None
    receipt_mime_type: str | None
    receipt_file_name: str | None
    user_receipt_message_id: int | None
    admin_chat_id: int | None
    admin_message_id: int | None
    admin_message_thread_id: int | None
    reviewed_by_telegram_id: int | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    subscription_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaymentUserResponse(BaseModel):
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None

    model_config = {"from_attributes": True}


class SubscriptionResponse(BaseModel):
    id: int
    plan_id: int
    started_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class PaymentActionResponse(BaseModel):
    payment: PaymentResponse
    user: PaymentUserResponse
    subscription: SubscriptionResponse | None = None
    already_reviewed: bool = False


class CurrentSubscriptionResponse(BaseModel):
    is_active: bool
    plan_slug: str | None = None
    plan_name: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
