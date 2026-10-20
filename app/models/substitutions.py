from datetime import date

from pydantic import BaseModel, Field


class SubstitutionCreate(BaseModel):
    date: date
    period: int = Field(ge=1, le=12)
    substitute_id: int
    leave_id: int | None = None
    note: str | None = Field(default=None, max_length=1000)
    teacher_id: int | None = Field(
        default=None, description="Managers only: plan for this teacher (default: the caller)"
    )


class SubstitutionResponse(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
