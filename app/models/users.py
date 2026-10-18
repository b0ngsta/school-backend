from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import settings
from app.core.validators import EMAIL_REQUIRED, clean_email
from app.models.common import PASSWORD_MAX_LENGTH, StaffType


class TeacherInfo(BaseModel):
    subject: str | None = None
    qualification: str | None = None
    joining_date: date | None = None
    address: str | None = None


class TeacherInfoUpdate(TeacherInfo):
    pass


class StaffCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)
    full_name: str
    email: str | None = None
    phone: str | None = None
    user_type: StaffType
    teacher_info: TeacherInfo | None = None

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        return clean_email(value)

    @model_validator(mode="after")
    def _email_required(self):
        if settings.require_user_email and not (self.email or "").strip():
            raise ValueError(EMAIL_REQUIRED)
        return self


class StaffUpdate(BaseModel):
    """PUT /users/{id}: fields left out of the request keep their value."""

    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = None
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        return clean_email(value)

    @model_validator(mode="after")
    def _email_required(self):
        if settings.require_user_email and "email" in self.model_fields_set and not (self.email or "").strip():
            raise ValueError(EMAIL_REQUIRED)
        return self
