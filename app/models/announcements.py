from datetime import date

from pydantic import BaseModel, Field, model_validator


class AnnouncementSave(BaseModel):
    """Create / replace an app-launch announcement. Without dates it shows
    from now on until switched off."""

    title: str = Field(min_length=1, max_length=150)
    body: str | None = Field(default=None, max_length=4000)
    starts_on: date | None = None
    ends_on: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _dates(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        return self
