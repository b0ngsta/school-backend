from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

UserType = Literal["admin", "sub_admin", "teacher", "student"]


# ---------- auth ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    user_type: UserType
    full_name: str


# ---------- users ----------
class TeacherInfo(BaseModel):
    subject: str | None = None
    qualification: str | None = None
    joining_date: date | None = None
    address: str | None = None


class StudentInfo(BaseModel):
    class_id: int | None = None
    section_id: int | None = None
    roll_no: str | None = None
    guardian_name: str | None = None
    guardian_phone: str | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    full_name: str
    email: str | None = None
    phone: str | None = None
    user_type: UserType
    teacher_info: TeacherInfo | None = None   # when user_type == teacher
    student_info: StudentInfo | None = None   # when user_type == student


class TeacherInfoUpdate(TeacherInfo):
    pass


# ---------- classes ----------
class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class SectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=10)


# ---------- report cards ----------
class ReportCardCreate(BaseModel):
    student_id: int
    term: str
    grades: dict[str, Any] | None = None   # {"Math": 92, "Science": "A"}
    remarks: str | None = None


# ---------- notices ----------
class NoticeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str
