"""Student-facing endpoints for the mobile app.

Everything here is scoped to the logged-in student ("me") — no IDs are
accepted from the client, so a student can only ever see their own data.
Add new student features to this router.
"""
from fastapi import APIRouter, Depends, Response

from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
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
                user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return paged(response, portal.homework(db, user.id, page))


@router.get("/remarks")
def my_remarks(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.remarks(db, user.id)


@router.get("/report-cards")
def my_report_cards(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.report_cards(db, user.id)


@router.get("/attendance")
def my_attendance(user: CurrentUser = Depends(me), db: Db = Depends(get_db)):
    return portal.attendance(db, user.id)


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
