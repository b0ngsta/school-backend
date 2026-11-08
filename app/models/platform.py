from pydantic import BaseModel, Field

from app.models.common import PASSWORD_MAX_LENGTH


class FeaturesUpdate(BaseModel):
    """Switch features on / off for one school (keys from GET /platform/features).
    true = on, false = off, null = back to the default (on)."""

    changes: dict[str, bool | None] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=255, description='Why, e.g. "Not in their plan"')


class PlatformPasswordReset(BaseModel):
    """A new password for one school account, set by the platform operator
    (e.g. a school's admin who is locked out)."""

    new_password: str = Field(min_length=8, max_length=PASSWORD_MAX_LENGTH)
    must_change_password: bool = Field(
        default=True, description="Make the user choose their own password at the next login"
    )
