from typing import Literal

from pydantic import BaseModel, Field

from app.models.common import PaymentMethod


class TransactionCreate(BaseModel):
    student_id: int | None = None
    amount: float = Field(gt=0)
    method: PaymentMethod
    status: Literal["success", "pending", "failed"] = "success"
