"""The columns of the bulk-import CSV, per user type.

This list is the single place to change when a field is added: add a
`Column` here (and the database column), and the template, the
`/imports/users/columns` description, validation, comparison on re-upload
and the export all follow. Headers are matched case-insensitively, spaces
and dashes count as underscores, and a few common aliases are accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.validators import EMAIL_MAX_LENGTH
from app.models.common import PASSWORD_MAX_LENGTH

Kind = Literal["username", "text", "email", "phone", "date", "password", "photo"]
Table = Literal["users", "student_profiles", "teacher_profiles"]


@dataclass(frozen=True)
class Column:
    name: str
    description: str
    kind: Kind = "text"
    table: Table | None = "users"      # None: not stored as-is (password, photo)
    max_length: int | None = None
    min_length: int | None = None
    required: bool = False             # needed to create a user (blank on re-upload keeps the value)
    example: str = ""
    aliases: tuple[str, ...] = ()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "required": self.required,
            "description": self.description,
            "format": _FORMATS[self.kind],
            "max_length": self.max_length,
            "example": self.example,
        }


_FORMATS: dict[Kind, str] = {
    "username": "text",
    "text": "text",
    "email": "email",
    "phone": "digits, spaces, + - ( )",
    "date": "YYYY-MM-DD or DD-MM-YYYY",
    "password": "text, 6+ characters",
    "photo": "file name inside the photos ZIP",
}

USERNAME = Column("username", "Login name. Identifies the user when the file is uploaded again; cannot be "
                  "changed by CSV.", kind="username", min_length=3, max_length=50, required=True,
                  example="rahul.k", aliases=("user_name", "login"))
PASSWORD = Column("password", "First password for NEW users (they are asked to change it). Ignored for "
                  "existing users.", kind="password", table=None, min_length=6, max_length=PASSWORD_MAX_LENGTH,
                  required=True, example="Start@123")
FULL_NAME = Column("full_name", "Full name", max_length=100, required=True, example="Rahul Kumar",
                   aliases=("name", "student_name"))
EMAIL = Column("email", "Email address (used for password resets)", kind="email", max_length=EMAIL_MAX_LENGTH,
               required=True, example="rahul.parent@gmail.com", aliases=("email_address", "e_mail", "mail"))
PHONE = Column("phone", "Phone number", kind="phone", max_length=20, example="+91 98765 43210",
               aliases=("mobile", "mobile_no", "phone_no", "contact"))
PHOTO = Column("photo", "Profile photo: the file name inside the photos ZIP (JPEG/PNG/WebP, max 5 MB)",
               kind="photo", table=None, max_length=255, example="ADM1023.jpg",
               aliases=("photo_file", "photo_filename", "image"))

STUDENT_FIELDS = (
    Column("admission_no", "Admission number (unique in the school)", table="student_profiles", max_length=30,
           example="ADM1023", aliases=("admission_number", "adm_no", "admission")),
    Column("roll_no", "Roll number (unique in the section)", table="student_profiles", max_length=20,
           example="12", aliases=("roll_number", "roll")),
    Column("dob", "Date of birth", kind="date", table="student_profiles", example="2014-05-21",
           aliases=("date_of_birth", "birth_date")),
    Column("father_name", "Father's name", table="student_profiles", max_length=100, example="Suresh Kumar",
           aliases=("father", "fathers_name")),
    Column("mother_name", "Mother's name", table="student_profiles", max_length=100, example="Anita Kumar",
           aliases=("mother", "mothers_name")),
    Column("guardian_phone", "Parent/guardian phone", kind="phone", table="student_profiles", max_length=20,
           example="+91 98765 43210", aliases=("parent_phone", "guardian_mobile", "parent_mobile")),
    Column("address", "Home address", table="student_profiles", max_length=255, example="12 MG Road, Pune"),
)

TEACHER_FIELDS = (
    Column("subject", "Main subject", table="teacher_profiles", max_length=100, example="Mathematics"),
    Column("qualification", "Qualification", table="teacher_profiles", max_length=200, example="M.Sc, B.Ed"),
    Column("joining_date", "Date of joining", kind="date", table="teacher_profiles", example="2021-06-01",
           aliases=("date_of_joining", "doj")),
    Column("address", "Address", table="teacher_profiles", max_length=255, example="45 Park Street, Kolkata"),
)

USER_TYPES = ("admin", "principal", "sub_admin", "coordinator", "teacher", "driver", "student")


def columns_for(user_type: str) -> tuple[Column, ...]:
    extra = STUDENT_FIELDS if user_type == "student" else TEACHER_FIELDS if user_type == "teacher" else ()
    return (USERNAME, PASSWORD, FULL_NAME, EMAIL, PHONE, *extra, PHOTO)


def header_key(header: str) -> str:
    """'Full Name' / 'full-name' / ' FULL_NAME ' -> 'full_name'."""
    cleaned = header.strip().lstrip("﻿").lower().replace("-", " ").replace(".", " ")
    return "_".join(cleaned.split())


def resolve_headers(user_type: str) -> dict[str, Column]:
    """Every accepted header spelling -> its column."""
    lookup: dict[str, Column] = {}
    for column in columns_for(user_type):
        for spelling in (column.name, *column.aliases):
            lookup[header_key(spelling)] = column
    return lookup
