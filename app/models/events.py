from datetime import date, time

from pydantic import BaseModel, Field, field_validator, model_validator


class EventSave(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=4000)
    venue: str | None = Field(default=None, max_length=150)
    start_date: date
    end_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None

    @model_validator(mode="after")
    def _dates(self):
        if self.end_date is None:
            self.end_date = self.start_date
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class EventVideo(BaseModel):
    url: str = Field(min_length=8, max_length=500, description="https link (YouTube, Google Drive, …)")
    caption: str | None = Field(default=None, max_length=200)

    @field_validator("url")
    @classmethod
    def _https(cls, value: str) -> str:
        value = value.strip()
        if not value.lower().startswith(("https://", "http://")) or " " in value:
            raise ValueError("Enter a full video link starting with https://")
        return value
