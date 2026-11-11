import re

from pydantic import BaseModel, Field, field_validator

from app.core.validators import clean_email

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class SchoolProfileUpdate(BaseModel):
    """PUT /school (and the owner portal): fields left out keep their value;
    an empty string clears a field."""

    director_name: str | None = Field(default=None, max_length=100)
    principal_name: str | None = Field(default=None, max_length=100)
    affiliation_no: str | None = Field(default=None, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    tel: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    session_start_month: int | None = Field(default=None, ge=1, le=12,
                                            description="Month the academic session starts (4 = April)")
    # the school's theme (migration 0022): "#RRGGBB"; an empty string = back to the apps' own colour
    theme_color: str | None = Field(default=None, max_length=7, description="Main colour")
    header_color: str | None = Field(default=None, max_length=7, description="Header colour (empty = main colour)")
    button_color: str | None = Field(default=None, max_length=7, description="Button colour (empty = main colour)")

    @field_validator("director_name", "principal_name", "affiliation_no", "phone", "tel", "website", "address")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @field_validator("theme_color", "header_color", "button_color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        value = _blank_to_none(value)
        if value is None:
            return None
        if not _HEX_COLOR.match(value):
            raise ValueError("Enter the colour as # and six characters, e.g. #2B4C7E")
        return value.upper()

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return _blank_to_none(clean_email(value))
