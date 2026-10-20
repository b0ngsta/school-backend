"""Students + their academic records (homework, remarks, report cards).

Permissions:
- view: managers, or any teacher assigned to the student's class/section
- add/edit students: managers, or the section's CLASS teacher
- records: managers, or any teacher assigned to the section
"""
from fastapi import APIRouter, Depends, Query, Response

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.students import (
    HomeworkCreate,
    HomeworkUpdate,
    RemarkCreate,
    ReportCardCreate,
    StudentCreate,
    StudentUpdate,
)
from app.services import students as student_service
from app.services.pagination import Page

router = APIRouter(prefix="/students", tags=["students"])

staff = require_roles(*STAFF)

HomeworkScope = Query(None, pattern="^(class|student)$", description="class = a class homework (/homework)")


@router.post("", status_code=201)
def create_student(body: StudentCreate, actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.create_student(db, actor, body)


@router.get("")
def list_students(
    response: Response,
    class_id: int | None = None,
    section_id: int | None = None,
    page: Page = Depends(page_params),
    user: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    return paged(response, student_service.list_students(db, user, class_id, section_id, page))


@router.get("/{student_id}")
def get_student(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.view_student(db, user, student_id)


@router.put("/{student_id}")
def update_student(student_id: int, body: StudentUpdate, user: CurrentUser = Depends(staff),
                   db: Db = Depends(get_db)):
    return student_service.update_student(db, user, student_id, body)


@router.delete("/{student_id}")
def delete_student(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.delete_student(db, user, student_id)


# ================= attendance history =================

@router.get("/{student_id}/attendance")
def student_attendance(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.attendance_history(db, user, student_id)


# ================= homework =================

@router.get("/{student_id}/homework")
def list_homework(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.list_homework(db, user, student_id)


@router.post("/{student_id}/homework", status_code=201)
def add_homework(student_id: int, body: HomeworkCreate, user: CurrentUser = Depends(staff),
                 db: Db = Depends(get_db)):
    return student_service.add_homework(db, user, student_id, body)


@router.put("/{student_id}/homework/{homework_id}")
def update_homework(student_id: int, homework_id: int, body: HomeworkUpdate, scope: str | None = HomeworkScope,
                    user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """`?scope=class`: set this student's status on a class homework (`id` = class homework id)."""
    return student_service.update_homework(db, user, student_id, homework_id, body, scope)


@router.delete("/{student_id}/homework/{homework_id}")
def delete_homework(student_id: int, homework_id: int, scope: str | None = HomeworkScope,
                    user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Per-student homework only; class homework (`?scope=class`) → 409."""
    return student_service.delete_homework(db, user, student_id, homework_id, scope)


# ================= remarks =================

@router.get("/{student_id}/remarks")
def list_remarks(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.list_remarks(db, user, student_id)


@router.post("/{student_id}/remarks", status_code=201)
def add_remark(student_id: int, body: RemarkCreate, user: CurrentUser = Depends(staff),
               db: Db = Depends(get_db)):
    return student_service.add_remark(db, user, student_id, body)


@router.delete("/{student_id}/remarks/{remark_id}")
def delete_remark(student_id: int, remark_id: int, user: CurrentUser = Depends(staff),
                  db: Db = Depends(get_db)):
    return student_service.delete_remark(db, user, student_id, remark_id)


# ================= report cards =================

@router.get("/{student_id}/report-cards")
def list_report_cards(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.list_report_cards(db, user, student_id)


@router.post("/{student_id}/report-cards", status_code=201)
def add_report_card(student_id: int, body: ReportCardCreate, user: CurrentUser = Depends(staff),
                    db: Db = Depends(get_db)):
    return student_service.add_report_card(db, user, student_id, body)


@router.delete("/{student_id}/report-cards/{report_id}")
def delete_report_card(student_id: int, report_id: int, user: CurrentUser = Depends(staff),
                       db: Db = Depends(get_db)):
    return student_service.delete_report_card(db, user, student_id, report_id)
