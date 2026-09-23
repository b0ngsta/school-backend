from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

UserType = Literal[
    "admin", "principal", "sub_admin", "coordinator", "driver", "teacher", "student"
]
StaffType = Literal["admin", "principal", "sub_admin", "coordinator", "driver", "teacher"]


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


# ---------- staff users ----------
class TeacherInfo(BaseModel):
    subject: str | None = None
    qualification: str | None = None
    joining_date: date | None = None
    address: str | None = None


class StaffCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    full_name: str
    email: str | None = None
    phone: str | None = None
    user_type: StaffType
    teacher_info: TeacherInfo | None = None


class TeacherInfoUpdate(TeacherInfo):
    pass


# ---------- assignments ----------
class AssignmentCreate(BaseModel):
    teacher_id: int
    class_id: int
    section_id: int
    role: Literal["class_teacher", "subject_teacher"]
    subject: str | None = None  # for subject_teacher


# ---------- classes ----------
class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    fee_amount: float | None = Field(default=None, ge=0)
    subjects: list[str] = Field(default_factory=list)


class SubjectsUpdate(BaseModel):
    subjects: list[str] = Field(default_factory=list)


class SectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=10)


# ---------- students ----------
class StudentBase(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    roll_no: str | None = None
    admission_no: str | None = None
    dob: date | None = None
    father_name: str | None = None
    mother_name: str | None = None
    guardian_phone: str | None = None
    address: str | None = None


class StudentCreate(StudentBase):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    class_id: int
    section_id: int


class StudentUpdate(StudentBase):
    class_id: int | None = None
    section_id: int | None = None


# ---------- student page records ----------
class HomeworkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    status: Literal["pending", "submitted", "late"] = "pending"
    note: str | None = None


class HomeworkUpdate(BaseModel):
    title: str | None = None
    due_date: date | None = None
    status: Literal["pending", "submitted", "late"] | None = None
    note: str | None = None


class RemarkCreate(BaseModel):
    remark: str = Field(min_length=1)


class ReportCardCreate(BaseModel):
    term: str
    grades: dict[str, Any] | None = None
    remarks: str | None = None


# ---------- notices ----------
class NoticeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str
