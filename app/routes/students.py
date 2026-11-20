"""Students + their academic records (homework, remarks, report cards).

Permissions:
- view: managers, or any teacher assigned to the student's class/section
- add/edit students: managers, or the section's CLASS teacher
- records: managers, or any teacher assigned to the section
"""
from fastapi import APIRouter, Depends, Query, Response

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_feature, require_roles
from app.models.auth import CurrentUser
from app.models.students import (
    HomeworkCreate,
    HomeworkUpdate,
    RemarkCreate,
    RemarkUpdate,
    ReportCardCreate,
    StudentCreate,
    StudentUpdate,
    StudentUsername,
)
from app.services import final_results, leaving, report_card_files, siblings
from app.services import students as student_service
from app.services.pagination import Page

router = APIRouter(prefix="/students", tags=["students"])

staff = require_roles(*STAFF)
manager = require_roles(*MANAGERS)

HomeworkScope = Query(None, pattern="^(class|student)$", description="class = a class homework (/homework)")


@router.post("", status_code=201)
def create_student(body: StudentCreate, actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.create_student(db, actor, body)


@router.get("")
def list_students(
    response: Response,
    class_id: int | None = None,
    section_id: int | None = None,
    status: str = Query("active", pattern="^(active|left|all)$",
                        description="active (default) · left (marked as left; managers only) · all"),
    q: str | None = Query(None, max_length=100, description="Name, username, admission or roll number"),
    page: Page = Depends(page_params),
    user: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    return paged(response, student_service.list_students(db, user, class_id, section_id, page, status, q))


@router.get("/username-check")
def username_check(
    username: str = Query(..., min_length=1, max_length=50),
    student_id: int | None = Query(None, description="The student being renamed (their own username counts as free)"),
    user: CurrentUser = Depends(manager),
    db: Db = Depends(get_db),
):
    """Is `username` free for a student of this school? The app's "Change username"
    Verify button. `available`; `current` (it is already this student's); `taken_by_staff`
    (a staff / driver account has it: pick another); `shared_with` (students who have it:
    pick another, or confirm they are brothers / sisters and save with `sibling: true`)."""
    return siblings.username_check(db, username, student_id)


@router.get("/{student_id}")
def get_student(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.view_student(db, user, student_id)


@router.put("/{student_id}")
def update_student(student_id: int, body: StudentUpdate, user: CurrentUser = Depends(staff),
                   db: Db = Depends(get_db)):
    return student_service.update_student(db, user, student_id, body)


@router.put("/{student_id}/username")
def change_username(student_id: int, body: StudentUsername, user: CurrentUser = Depends(manager),
                    db: Db = Depends(get_db)):
    """The office gives a student another username, e.g. the one a brother or sister
    has, so the parent can switch between them in the app. A username another student
    has: 409 `code: "username_shared"` with `siblings`; send again with `sibling: true`
    to share it. A staff username: 409. The password stays as it is."""
    return siblings.rename_student(db, user, student_id, body)


@router.delete("/{student_id}")
def delete_student(student_id: int, reason: str | None = Query(None, max_length=255),
                   user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Managers: mark the student as left (TC issued, moved away…). Fees,
    payments, marks and attendance are kept; the student leaves class lists."""
    return student_service.delete_student(db, user, student_id, reason)


@router.post("/{student_id}/reactivate")
def reactivate_student(student_id: int, user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Bring a left student back (into their last section when it still exists)."""
    student_service.get_student(db, student_id)
    return leaving.reactivate(db, user, student_id)


# ================= attendance history =================

@router.get("/{student_id}/attendance", dependencies=[Depends(require_feature("attendance"))])
def student_attendance(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.attendance_history(db, user, student_id)


@router.get("/{student_id}/attendance/month", dependencies=[Depends(require_feature("attendance"))])
def student_attendance_month(student_id: int, year: int, month: int, user: CurrentUser = Depends(staff),
                             db: Db = Depends(get_db)):
    """Every day of one month (present / absent / leave / half_day / holiday / sunday /
    not_marked) + summary — the parent app's calendar, for staff who can view the student."""
    return student_service.attendance_month(db, user, student_id, year, month)


@router.get("/{student_id}/attendance/overview", dependencies=[Depends(require_feature("attendance"))])
def student_attendance_overview(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.attendance_overview(db, user, student_id)


# ================= transport =================

@router.get("/{student_id}/transport", dependencies=[Depends(require_feature("transport"))])
def student_transport(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Vehicle, driver, stop and times (null = not on school transport). Change it with
    PUT / DELETE /vehicles/{vehicle_id}/students/{student_id} (managers)."""
    return student_service.transport_of(db, user, student_id)


# ================= exam results =================

@router.get("/{student_id}/exam-results")
def student_exam_results(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Every exam the student has marks in + the session's final result
    (managers, or teachers assigned to the student's section)."""
    return final_results.student_results(db, user, student_id)


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


@router.put("/{student_id}/remarks/{remark_id}")
def update_remark(student_id: int, remark_id: int, body: RemarkUpdate, user: CurrentUser = Depends(staff),
                  db: Db = Depends(get_db)):
    """Change a remark's text: the teacher who wrote it, or managers."""
    return student_service.update_remark(db, user, student_id, remark_id, body)


@router.delete("/{student_id}/remarks/{remark_id}")
def delete_remark(student_id: int, remark_id: int, user: CurrentUser = Depends(staff),
                  db: Db = Depends(get_db)):
    return student_service.delete_remark(db, user, student_id, remark_id)


# ================= report cards =================

@router.get("/{student_id}/report-cards")
def list_report_cards(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return student_service.list_report_cards(db, user, student_id)


@router.get("/{student_id}/report-card-files")
def list_report_card_files(student_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Report-card PDFs released to this student's parents."""
    return report_card_files.student_files(db, user, student_id)


@router.post("/{student_id}/report-cards", status_code=201)
def add_report_card(student_id: int, body: ReportCardCreate, user: CurrentUser = Depends(staff),
                    db: Db = Depends(get_db)):
    return student_service.add_report_card(db, user, student_id, body)


@router.delete("/{student_id}/report-cards/{report_id}")
def delete_report_card(student_id: int, report_id: int, user: CurrentUser = Depends(staff),
                       db: Db = Depends(get_db)):
    return student_service.delete_report_card(db, user, student_id, report_id)
