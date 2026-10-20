from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

SubmissionStatus = Literal["pending", "submitted", "late"]


class ClassHomeworkCreate(BaseModel):
    class_id: int
    section_id: int
    title: str = Field(min_length=1, max_length=200)
    subject: str | None = Field(default=None, max_length=100)
    note: str | None = None
    due_date: date | None = None


class ClassHomeworkUpdate(BaseModel):
    """Same body as create; the class and section can't change."""

    class_id: int | None = None
    section_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    subject: str | None = Field(default=None, max_length=100)
    note: str | None = None
    due_date: date | None = None


class SubmissionItem(BaseModel):
    student_id: int
    status: SubmissionStatus


class SubmissionsUpdate(BaseModel):
    items: list[SubmissionItem] = Field(max_length=1000)
