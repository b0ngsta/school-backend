"""HR features for staff (teachers, managers, drivers):

- /holidays          school holiday calendar (managers CRUD, everyone reads)
- /leaves            leave requests (staff submit, managers approve/reject)
- /salaries          monthly salary statements (managers create, staff view own)
- /staff-attendance  daily check-in / check-out with in/out times
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS, STAFF

router = APIRouter(tags=["hr"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF, "driver")  # every non-student role


# ====================== holidays ======================

class HolidayCreate(BaseModel):
    date: date
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=255)


@router.get("/holidays")
def list_holidays(year: int | None = None, user: dict = Depends(staff)):
    sql = (
        "SELECT h.*, u.full_name AS created_by_name FROM holidays h "
        "JOIN users u ON u.id = h.created_by"
    )
    params: tuple = ()
    if year:
        sql += " WHERE YEAR(h.date) = %s"
        params = (year,)
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY h.date", params)
        return cur.fetchall()


@router.post("/holidays", status_code=201)
def create_holiday(body: HolidayCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM holidays WHERE date = %s", (body.date,))
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "A holiday already exists on that date")
        cur.execute(
            "INSERT INTO holidays (date, name, description, created_by) VALUES (%s, %s, %s, %s)",
            (body.date, body.name, body.description, actor["id"]),
        )
        return {"id": cur.lastrowid}


@router.put("/holidays/{holiday_id}")
def update_holiday(holiday_id: int, body: HolidayCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM holidays WHERE date = %s AND id != %s", (body.date, holiday_id))
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "A holiday already exists on that date")
        cur.execute(
            "UPDATE holidays SET date=%s, name=%s, description=%s WHERE id=%s",
            (body.date, body.name, body.description, holiday_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Holiday not found")
    return {"updated": holiday_id}


@router.delete("/holidays/{holiday_id}")
def delete_holiday(holiday_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM holidays WHERE id = %s", (holiday_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Holiday not found")
    return {"deleted": holiday_id}


# ====================== leave requests ======================

LEAVE_SELECT = (
    "SELECT l.*, u.full_name, u.user_type, u.photo_path, r.full_name AS reviewed_by_name "
    "FROM leave_requests l "
    "JOIN users u ON u.id = l.user_id "
    "LEFT JOIN users r ON r.id = l.reviewed_by"
)


class LeaveCreate(BaseModel):
    from_date: date
    to_date: date
    reason: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def check_dates(self):
        if self.to_date < self.from_date:
            raise ValueError("to_date must be on/after from_date")
        return self


class LeaveReview(BaseModel):
    note: str | None = Field(default=None, max_length=255)


@router.post("/leaves", status_code=201)
def request_leave(body: LeaveCreate, actor: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM leave_requests WHERE user_id = %s AND status = 'pending'",
            (actor["id"],),
        )
        if cur.fetchone():
            raise HTTPException(
                status.HTTP_409_CONFLICT, "You already have a pending leave request"
            )
        cur.execute(
            "INSERT INTO leave_requests (user_id, from_date, to_date, reason) "
            "VALUES (%s, %s, %s, %s)",
            (actor["id"], body.from_date, body.to_date, body.reason),
        )
        return {"id": cur.lastrowid, "status": "pending"}


@router.get("/leaves/mine")
def my_leaves(actor: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            LEAVE_SELECT + " WHERE l.user_id = %s ORDER BY l.created_at DESC",
            (actor["id"],),
        )
        return cur.fetchall()


@router.get("/leaves")
def list_leaves(status_filter: str | None = None, actor: dict = Depends(manager)):
    sql, params = LEAVE_SELECT, ()
    if status_filter:
        sql += " WHERE l.status = %s"
        params = (status_filter,)
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY l.status = 'pending' DESC, l.created_at DESC", params)
        return cur.fetchall()


def _review_leave(leave_id: int, new_status: str, note: str | None, actor: dict):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM leave_requests WHERE id = %s", (leave_id,))
        leave = cur.fetchone()
        if not leave:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Leave request not found")
        if leave["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Already {leave['status']}")
        cur.execute(
            "UPDATE leave_requests SET status=%s, reviewed_by=%s, reviewed_at=NOW(), "
            "review_note=%s WHERE id=%s",
            (new_status, actor["id"], note, leave_id),
        )
    return {new_status: leave_id}


@router.put("/leaves/{leave_id}/approve")
def approve_leave(leave_id: int, body: LeaveReview, actor: dict = Depends(manager)):
    return _review_leave(leave_id, "approved", body.note, actor)


@router.put("/leaves/{leave_id}/reject")
def reject_leave(leave_id: int, body: LeaveReview, actor: dict = Depends(manager)):
    return _review_leave(leave_id, "rejected", body.note, actor)


# ====================== salaries ======================

class SalaryCreate(BaseModel):
    user_id: int
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)
    amount: float = Field(gt=0)
    status: str = Field(default="paid", pattern="^(paid|pending)$")
    note: str | None = Field(default=None, max_length=255)


@router.get("/salaries/mine")
def my_salaries(actor: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT s.*, u.full_name AS created_by_name FROM salary_records s "
            "JOIN users u ON u.id = s.created_by "
            "WHERE s.user_id = %s ORDER BY s.year DESC, s.month DESC",
            (actor["id"],),
        )
        return cur.fetchall()


@router.get("/salaries")
def list_salaries(user_id: int | None = None, actor: dict = Depends(manager)):
    sql = (
        "SELECT s.*, u.full_name, u.user_type FROM salary_records s "
        "JOIN users u ON u.id = s.user_id"
    )
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE s.user_id = %s"
        params = (user_id,)
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY s.year DESC, s.month DESC, u.full_name", params)
        return cur.fetchall()


@router.post("/salaries", status_code=201)
def create_salary(body: SalaryCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE id = %s AND user_type != 'student'",
            (body.user_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")
        cur.execute(
            "INSERT INTO salary_records (user_id, month, year, amount, status, note, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) AS new "
            "ON DUPLICATE KEY UPDATE amount=new.amount, status=new.status, note=new.note",
            (body.user_id, body.month, body.year, body.amount, body.status, body.note, actor["id"]),
        )
        return {"saved": True}


@router.delete("/salaries/{salary_id}")
def delete_salary(salary_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM salary_records WHERE id = %s", (salary_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Salary record not found")
    return {"deleted": salary_id}


# ====================== staff attendance ======================

@router.get("/staff-attendance/today")
def my_today(actor: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM staff_attendance WHERE user_id = %s AND date = CURDATE()",
            (actor["id"],),
        )
        return cur.fetchone() or {"in_time": None, "out_time": None, "date": str(date.today())}


@router.post("/staff-attendance/check-in")
def check_in(actor: dict = Depends(staff)):
    now = datetime.now().strftime("%H:%M:%S")
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, in_time FROM staff_attendance WHERE user_id = %s AND date = CURDATE()",
            (actor["id"],),
        )
        row = cur.fetchone()
        if row and row["in_time"]:
            raise HTTPException(status.HTTP_409_CONFLICT, "Already checked in today")
        if row:
            cur.execute("UPDATE staff_attendance SET in_time = %s WHERE id = %s", (now, row["id"]))
        else:
            cur.execute(
                "INSERT INTO staff_attendance (user_id, date, in_time) VALUES (%s, CURDATE(), %s)",
                (actor["id"], now),
            )
    return {"in_time": now}


@router.post("/staff-attendance/check-out")
def check_out(actor: dict = Depends(staff)):
    now = datetime.now().strftime("%H:%M:%S")
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, in_time FROM staff_attendance WHERE user_id = %s AND date = CURDATE()",
            (actor["id"],),
        )
        row = cur.fetchone()
        if not row or not row["in_time"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Check in first")
        cur.execute("UPDATE staff_attendance SET out_time = %s WHERE id = %s", (now, row["id"]))
    return {"out_time": now}


@router.get("/staff-attendance/mine")
def my_attendance_history(actor: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM staff_attendance WHERE user_id = %s ORDER BY date DESC LIMIT 60",
            (actor["id"],),
        )
        return cur.fetchall()


@router.get("/staff-attendance")
def staff_attendance_by_day(day: date | None = None, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT a.*, u.full_name, u.user_type, u.photo_path "
            "FROM staff_attendance a JOIN users u ON u.id = a.user_id "
            "WHERE a.date = %s ORDER BY u.full_name",
            (day or date.today(),),
        )
        return cur.fetchall()
