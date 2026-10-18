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


class ClaimReview(BaseModel):
    note: str | None = Field(default=None, max_length=255)
    method: PaymentMethod | None = None
