from typing import Literal

from pydantic import BaseModel


class AssignmentCreate(BaseModel):
    teacher_id: int
    class_id: int
    section_id: int
    role: Literal["class_teacher", "subject_teacher"]
    subject: str | None = None  # for subject_teacher
