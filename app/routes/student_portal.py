"""Student-facing endpoints for the mobile app.

Everything here is scoped to the logged-in student ("me") — no IDs are
accepted from the client, so a student can only ever see their own data.
Add new student features to this router.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.services import announcements, documents, messages, student_attendance, transport
from app.services import student_portal as portal
from app.services.pagination import Page

router = APIRouter(prefix="/student", tags=["student portal"])

me = require_roles("student")


@router.get("/me")
def my_profile(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.profile(db, user.id)


@router.get("/dashboard")
def my_dashboard(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.dashboard(db, user.id)


@router.get("/homework")
def my_homework(response: Response, page: Page = Depends(page_params),
                date: date | None = Query(None, description="Only homework given on this day"),
                subject: str | None = Query(None, max_length=100, description='Only this subject ("General" = none)'),
                user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return paged(response, portal.homework(db, user.id, page, date, subject))


@router.get("/homework/subjects")
def my_homework_subjects(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """The class's subjects with homework count and the day the latest was given."""
    return portal.homework_subjects(db, user.id)


@router.get("/remarks")
def my_remarks(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.remarks(db, user.id)


@router.get("/report-cards")
def my_report_cards(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.report_cards(db, user.id)


@router.get("/attendance")
def my_attendance(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.attendance(db, user.id)


@router.get("/attendance/overview")
def my_attendance_overview(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """Session totals (working days, present, half days, absent, holidays, %) + a row per month."""
    return student_attendance.overview(db, user.id)


@router.get("/attendance/month")
def my_attendance_month(year: int, month: int, user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """Every day of the month with its status, and the month's summary."""
    return student_attendance.month(db, user.id, year, month)


@router.get("/exams")
def my_exams(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.exams(db, user.id)


@router.get("/fees")
def my_fees(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.fees(db, user.id)


@router.get("/timetable")
def my_class_timetable(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """The weekly timetable of the student's section (with teacher names)."""
    return portal.timetable(db, user.id)


@router.get("/holidays")
def student_holidays(year: int | None = None, user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.holidays(db, year)


@router.get("/calendar")
def student_calendar(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """School calendar (events + exams + holidays) for the mobile app."""
    return portal.calendar(db)


@router.get("/notices")
def my_notices(response: Response, page: Page = Depends(page_params),
               user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return paged(response, portal.notices(db, page))


@router.get("/notices/{notice_id}")
def my_notice(notice_id: int, user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.notice(db, notice_id)


@router.get("/announcements")
def active_announcements(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """Shown in a popup every time the app starts (active, within their dates)."""
    return announcements.active_for_students(db)


@router.get("/documents")
def my_documents(response: Response, kind: Literal["timetable", "syllabus"], page: Page = Depends(page_params),
                 user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """Timetables / syllabus files for the whole school, the student's class or section."""
    return paged(response, documents.for_student(db, user.id, kind, page))


@router.get("/syllabus")
def my_syllabus(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """One entry per subject of the class, each with its syllabus files."""
    return documents.syllabus_for_student(db, user.id)


@router.get("/messages")
def my_messages(response: Response, page: Page = Depends(page_params),
                user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return paged(response, messages.for_student(db, user.id, page))


@router.get("/messages/unread-count")
def my_unread_messages(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return messages.unread_count(db, user.id)


@router.get("/messages/{message_id}")
def my_message(message_id: int, user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """The message; opening it marks it read."""
    return messages.open_for_student(db, user.id, message_id)


@router.get("/transport")
def my_transport(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """Vehicle, driver, stop and times — null when the student doesn't use school transport."""
    return transport.for_student(db, user.id)


@router.get("/teachers")
def my_teachers(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    """The section's teachers with their subjects (class teacher first)."""
    return portal.teachers(db, user.id)
