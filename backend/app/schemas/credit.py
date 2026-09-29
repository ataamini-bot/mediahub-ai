from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_BALANCE = Decimal("99999999999999.9999")


class CreditChange(BaseModel):
    actor_telegram_id: int = Field(gt=0)
    request_id: UUID
    currency: Literal["IRT", "USDT"]
    expected_version: int = Field(ge=0)
    action: Literal["credit", "debit", "reverse"]
    amount: Decimal | None = Field(default=None, gt=0, le=MAX_BALANCE, max_digits=18, decimal_places=4)
    entry_id: UUID | None = None
    reason: str = Field(min_length=5, max_length=500)

    @field_validator("amount", mode="before")
    @classmethod
    def exact_amount(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Send money as a decimal string, never a float")
        return value

    @model_validator(mode="after")
    def valid_change(self):
        self.reason = self.reason.strip()
        if len(self.reason) < 5:
            raise ValueError("A meaningful reason is required")
        if self.action == "reverse":
            if self.entry_id is None or self.amount is not None:
                raise ValueError("Reversal requires only the original entry")
        elif self.amount is None or self.entry_id is not None:
            raise ValueError("Adjustment requires only an amount")
        if self.amount is not None and self.currency == "IRT" and self.amount != self.amount.to_integral_value():
            raise ValueError("Toman amounts must be whole numbers")
        return self


class CreditNoticeAck(BaseModel):
    claim_token: UUID
    status: Literal["sent", "failed", "uncertain", "disabled", "retry"]
    retry_after: int = Field(default=0, ge=0, le=86400)

    @model_validator(mode="after")
    def valid_retry(self):
        if self.status == "retry" and self.retry_after < 1:
            raise ValueError("A definite Telegram flood wait is required")
        return self
