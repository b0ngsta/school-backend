from datetime import date
from typing import Literal

from pydantic import BaseModel


class AttendanceRecord(BaseModel):
    student_id: int
    status: Literal["present", "absent", "leave", "half_day"]


class AttendanceMark(BaseModel):
    section_id: int
    date: date
    records: list[AttendanceRecord]
