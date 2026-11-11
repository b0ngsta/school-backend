from datetime import date

from pydantic import BaseModel, Field

from app.models.common import PaymentMethod


class FeeCreate(BaseModel):
    student_id: int
    title: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0)
    due_date: date | None = None


class FeePay(BaseModel):
    method: PaymentMethod = "cash"


class FeeGenerate(BaseModel):
    class_id: int
    title: str = Field(min_length=1, max_length=100)
    due_date: date | None = None
    # this part of the class's fee (an instalment); the parts of a session add up to at most
    # the class's fee_amount. Left out (older apps): the whole class fee, as before.
    amount: float | None = Field(default=None, gt=0, le=10_000_000)


class FeeUnpay(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class ClaimReview(BaseModel):
    note: str | None = Field(default=None, max_length=255)
    method: PaymentMethod | None = None
