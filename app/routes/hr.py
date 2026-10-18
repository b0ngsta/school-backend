"""HR features for staff (teachers, managers, drivers):

- /holidays          school holiday calendar (managers CRUD, everyone reads)
- /leaves            leave requests (staff submit, managers approve/reject)
- /salaries          monthly salary statements (managers create, staff view own)
- /staff-attendance  daily check-in / check-out with in/out times
"""
from datetime import date

from fastapi import APIRouter, Depends, Response

from app.core.roles import MANAGERS, NON_STUDENT
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.hr import HolidayCreate, LeaveCreate, LeaveReview, SalaryCreate
from app.services import hr as hr_service
from app.services.pagination import Page

router = APIRouter(tags=["hr"])

manager = require_roles(*MANAGERS)
staff = require_roles(*NON_STUDENT)  # every non-student role


# ====================== holidays ======================

@router.get("/holidays")
def list_holidays(year: int | None = None, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return hr_service.list_holidays(db, year)


@router.post("/holidays", status_code=201)
def create_holiday(body: HolidayCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return hr_service.create_holiday(db, actor, body)


@router.put("/holidays/{holiday_id}")
def update_holiday(holiday_id: int, body: HolidayCreate, actor: CurrentUser = Depends(manager),
                   db: Db = Depends(get_db)):
    return hr_service.update_holiday(db, holiday_id, body)


@router.delete("/holidays/{holiday_id}")
def delete_holiday(holiday_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return hr_service.delete_holiday(db, holiday_id)


# ====================== leave requests ======================

@router.post("/leaves", status_code=201)
def request_leave(body: LeaveCreate, actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return hr_service.request_leave(db, actor, body)


@router.get("/leaves/mine")
def my_leaves(response: Response, page: Page = Depends(page_params),
              actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return paged(response, hr_service.my_leaves(db, actor, page))


@router.get("/leaves")
def list_leaves(response: Response, status_filter: str | None = None, page: Page = Depends(page_params),
                actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, hr_service.list_leaves(db, status_filter, page))


@router.put("/leaves/{leave_id}/approve")
def approve_leave(leave_id: int, body: LeaveReview, actor: CurrentUser = Depends(manager),
                  db: Db = Depends(get_db)):
    return hr_service.review_leave(db, actor, leave_id, "approved", body.note)


@router.put("/leaves/{leave_id}/reject")
def reject_leave(leave_id: int, body: LeaveReview, actor: CurrentUser = Depends(manager),
                 db: Db = Depends(get_db)):
    return hr_service.review_leave(db, actor, leave_id, "rejected", body.note)


# ====================== salaries ======================

@router.get("/salaries/mine")
def my_salaries(actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return hr_service.my_salaries(db, actor)


@router.get("/salaries")
def list_salaries(response: Response, user_id: int | None = None, page: Page = Depends(page_params),
                  actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, hr_service.list_salaries(db, user_id, page))


@router.post("/salaries", status_code=201)
def create_salary(body: SalaryCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return hr_service.save_salary(db, actor, body)


@router.delete("/salaries/{salary_id}")
def delete_salary(salary_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return hr_service.delete_salary(db, actor, salary_id)


# ====================== staff attendance ======================

@router.get("/staff-attendance/today")
def my_today(actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return hr_service.my_today(db, actor)


@router.post("/staff-attendance/check-in")
def check_in(actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return hr_service.check_in(db, actor)


@router.post("/staff-attendance/check-out")
def check_out(actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return hr_service.check_out(db, actor)


@router.get("/staff-attendance/mine")
def my_attendance_history(actor: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return hr_service.my_attendance_history(db, actor)


@router.get("/staff-attendance")
def staff_attendance_by_day(day: date | None = None, actor: CurrentUser = Depends(manager),
                            db: Db = Depends(get_db)):
    return hr_service.staff_attendance_by_day(db, day)
