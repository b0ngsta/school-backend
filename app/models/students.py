from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import settings
from app.core.validators import EMAIL_REQUIRED, clean_email
from app.models.common import PASSWORD_MAX_LENGTH


class StudentBase(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    roll_no: str | None = None
    admission_no: str | None = None
    dob: date | None = None
    father_name: str | None = None
    mother_name: str | None = None
    guardian_phone: str | None = None
    address: str | None = None

    @field_validator("roll_no", "admission_no")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        """Roll and admission numbers are unique, so a blank one means "none"."""
        value = (value or "").strip()
        return value or None

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        return clean_email(value)


class StudentCreate(StudentBase):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)
    class_id: int
    section_id: int

    @model_validator(mode="after")
    def _email_required(self):
        if settings.require_user_email and not (self.email or "").strip():
            raise ValueError(EMAIL_REQUIRED)
        return self


class StudentUpdate(StudentBase):
    """Fields left out of the request keep their current value."""

    class_id: int | None = None
    section_id: int | None = None

    @model_validator(mode="after")
    def _email_required(self):
        if settings.require_user_email and "email" in self.model_fields_set and not (self.email or "").strip():
            raise ValueError(EMAIL_REQUIRED)
        return self


class HomeworkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    status: Literal["pending", "submitted", "late"] = "pending"
    note: str | None = None


class HomeworkUpdate(BaseModel):
    title: str | None = None
    due_date: date | None = None
    status: Literal["pending", "submitted", "late"] | None = None
    note: str | None = None


class RemarkCreate(BaseModel):
    remark: str = Field(min_length=1)


class ReportCardCreate(BaseModel):
    term: str
    grades: dict[str, Any] | None = None
    remarks: str | None = None
