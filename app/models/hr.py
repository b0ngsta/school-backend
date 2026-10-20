from datetime import date

from pydantic import BaseModel, Field, model_validator


class HolidayCreate(BaseModel):
    date: date
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=255)


class LeaveSubstitution(BaseModel):
    """Ask `substitute_id` to take `period` on `date` (see /substitutions)."""

    date: date
    period: int = Field(ge=1, le=12)
    substitute_id: int


class LeaveCreate(BaseModel):
    from_date: date
    to_date: date
    reason: str = Field(min_length=1, max_length=255)
    substitutions: list[LeaveSubstitution] = Field(default=[], max_length=200)

    @model_validator(mode="after")
    def check_dates(self):
        if self.to_date < self.from_date:
            raise ValueError("to_date must be on/after from_date")
        return self


class LeaveUpdate(LeaveCreate):
    """PUT /leaves/{id}: new dates/reason; `substitutions` are new requests to add."""


class LeaveReview(BaseModel):
    note: str | None = Field(default=None, max_length=255)


class SalaryCreate(BaseModel):
    user_id: int
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)
    amount: float = Field(gt=0)
    status: str = Field(default="paid", pattern="^(paid|pending)$")
    note: str | None = Field(default=None, max_length=255)
