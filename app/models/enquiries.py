from typing import Literal

from pydantic import BaseModel, Field

EnquiryStatus = Literal["new", "follow_up", "converted", "closed"]


class EnquiryCreate(BaseModel):
    parent_name: str = Field(min_length=1, max_length=100)
    student_name: str = Field(min_length=1, max_length=100)
    class_interested: str | None = None
    contact: str = Field(min_length=5, max_length=20)
    email: str | None = None
    notes: str | None = None


class EnquiryUpdate(BaseModel):
    parent_name: str | None = None
    student_name: str | None = None
    class_interested: str | None = None
    contact: str | None = None
    email: str | None = None
    notes: str | None = None
    status: EnquiryStatus | None = None
