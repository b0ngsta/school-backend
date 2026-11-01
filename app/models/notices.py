from pydantic import BaseModel, Field


class NoticeCreate(BaseModel):
    """A notice-board post. `class_ids` empty / left out = the whole school;
    otherwise only those classes' students (and their staff) see it and get
    the notification."""

    title: str = Field(min_length=1, max_length=200)
    body: str
    class_ids: list[int] | None = Field(default=None, max_length=100)
