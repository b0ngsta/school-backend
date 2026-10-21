from pydantic import BaseModel, Field, field_validator

from app.core.validators import clean_email
from app.models.common import PASSWORD_MAX_LENGTH


class OrganisationCreate(BaseModel):
    """A new school, with its first admin account (who then adds everyone else)."""

    code: str = Field(min_length=2, max_length=30, description="Typed at login, e.g. XYZPS; letters, digits, '-'")
    name: str = Field(min_length=1, max_length=150, description="e.g. XYZ Public School")
    admin_username: str = Field(default="admin", min_length=3, max_length=50)
    admin_password: str = Field(min_length=8, max_length=PASSWORD_MAX_LENGTH)
    admin_full_name: str = Field(default="Admin", min_length=1, max_length=100)
    admin_email: str | None = None

    @field_validator("admin_email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return clean_email(value)


class OrganisationUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=30,
                             description="New login code (users must type the new one from then on)")
    name: str | None = Field(default=None, min_length=1, max_length=150)
    is_active: bool | None = None
