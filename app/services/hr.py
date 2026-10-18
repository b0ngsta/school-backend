"""HR for staff (teachers, managers, drivers): holidays, leave requests,
salary statements and daily check-in / check-out."""
from __future__ import annotations

from datetime import date

from app.core.clock import school_now, school_today
from app.core.errors import BadRequest, Conflict, DuplicateKey, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.hr import HolidayCreate, LeaveCreate, SalaryCreate
from app.services import audit, pagination

# ====================== holidays ======================


def list_holidays(db: Db, year: int | None) -> list[dict]:
    sql = (
        "SELECT h.*, u.full_name AS created_by_name FROM holidays h "
        "JOIN users u ON u.id = h.created_by"
    )
    params: tuple = ()
    if year:
        sql += " WHERE YEAR(h.date) = %s"
        params = (year,)
    return db.all(sql + " ORDER BY h.date", params)


def create_holiday(db: Db, actor: CurrentUser, body: HolidayCreate) -> dict:
    if db.one("SELECT id FROM holidays WHERE date = %s", (body.date,)):
        raise Conflict("A holiday already exists on that date")
    try:
        holiday_id = db.execute(
            "INSERT INTO holidays (date, name, description, created_by) VALUES (%s, %s, %s, %s)",
            (body.date, body.name, body.description, actor.id),
        ).lastrowid
    except DuplicateKey:
        raise Conflict("A holiday already exists on that date") from None
    return {"id": holiday_id}


def update_holiday(db: Db, holiday_id: int, body: HolidayCreate) -> dict:
    if db.one("SELECT id FROM holidays WHERE date = %s AND id != %s", (body.date, holiday_id)):
        raise Conflict("A holiday already exists on that date")
    try:
        changed = db.execute(
            "UPDATE holidays SET date=%s, name=%s, description=%s WHERE id=%s",
            (body.date, body.name, body.description, holiday_id),
        ).rowcount
    except DuplicateKey:
        raise Conflict("A holiday already exists on that date") from None
    if changed == 0 and not db.one("SELECT id FROM holidays WHERE id = %s", (holiday_id,)):
        raise NotFound("Holiday not found")
    return {"updated": holiday_id}


def delete_holiday(db: Db, holiday_id: int) -> dict:
    if db.execute("DELETE FROM holidays WHERE id = %s", (holiday_id,)).rowcount == 0:
        raise NotFound("Holiday not found")
    return {"deleted": holiday_id}


# ====================== leave requests ======================

LEAVE_SELECT = (
    "SELECT l.*, u.full_name, u.user_type, u.photo_path, r.full_name AS reviewed_by_name "
    "FROM leave_requests l "
    "JOIN users u ON u.id = l.user_id "
    "LEFT JOIN users r ON r.id = l.reviewed_by"
)


def request_leave(db: Db, actor: CurrentUser, body: LeaveCreate) -> dict:
    with db.transaction():
        # lock the requester's row so two quick submits can't both pass the check
        db.one("SELECT id FROM users WHERE id = %s FOR UPDATE", (actor.id,))
        if db.one("SELECT id FROM leave_requests WHERE user_id = %s AND status = 'pending'", (actor.id,)):
            raise Conflict("You already have a pending leave request")
        leave_id = db.execute(
            "INSERT INTO leave_requests (user_id, from_date, to_date, reason) VALUES (%s, %s, %s, %s)",
            (actor.id, body.from_date, body.to_date, body.reason),
        ).lastrowid
    return {"id": leave_id, "status": "pending"}


def my_leaves(db: Db, actor: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(db, LEAVE_SELECT + " WHERE l.user_id = %s", (actor.id,),
                            "l.created_at DESC, l.id DESC", page)


def list_leaves(db: Db, status_filter: str | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql, params = LEAVE_SELECT, ()
    if status_filter:
        sql += " WHERE l.status = %s"
        params = (status_filter,)
    return pagination.fetch(db, sql, params, "l.status = 'pending' DESC, l.created_at DESC, l.id DESC", page)


def review_leave(db: Db, actor: CurrentUser, leave_id: int, new_status: str, note: str | None) -> dict:
    with db.transaction():
        leave = db.one("SELECT * FROM leave_requests WHERE id = %s FOR UPDATE", (leave_id,))
        if not leave:
            raise NotFound("Leave request not found")
        if leave["status"] != "pending":
            raise Conflict(f"Already {leave['status']}")
        db.execute(
            "UPDATE leave_requests SET status=%s, reviewed_by=%s, reviewed_at=NOW(), review_note=%s "
            "WHERE id=%s AND status='pending'",
            (new_status, actor.id, note, leave_id),
        )
        audit.record(db, actor, f"leave.{new_status}", "leave_request", leave_id, {"user_id": leave["user_id"]})
    return {new_status: leave_id}


# ====================== salaries ======================

def my_salaries(db: Db, actor: CurrentUser) -> list[dict]:
    return db.all(
        "SELECT s.*, u.full_name AS created_by_name FROM salary_records s "
        "JOIN users u ON u.id = s.created_by "
        "WHERE s.user_id = %s ORDER BY s.year DESC, s.month DESC",
        (actor.id,),
    )


def list_salaries(db: Db, user_id: int | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql = "SELECT s.*, u.full_name, u.user_type FROM salary_records s JOIN users u ON u.id = s.user_id"
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE s.user_id = %s"
        params = (user_id,)
    return pagination.fetch(db, sql, params, "s.year DESC, s.month DESC, u.full_name, s.id", page)


def save_salary(db: Db, actor: CurrentUser, body: SalaryCreate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM users WHERE id = %s AND user_type != 'student'", (body.user_id,)):
            raise NotFound("Staff member not found")
        db.execute(
            "INSERT INTO salary_records (user_id, month, year, amount, status, note, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE amount = VALUES(amount), status = VALUES(status), note = VALUES(note)",
            (body.user_id, body.month, body.year, body.amount, body.status, body.note, actor.id),
        )
        audit.record(db, actor, "salary.save", "user", body.user_id, body.model_dump())
    return {"saved": True}


def delete_salary(db: Db, actor: CurrentUser, salary_id: int) -> dict:
    with db.transaction():
        if db.execute("DELETE FROM salary_records WHERE id = %s", (salary_id,)).rowcount == 0:
            raise NotFound("Salary record not found")
        audit.record(db, actor, "salary.delete", "salary_record", salary_id)
    return {"deleted": salary_id}


# ====================== staff attendance ======================
# Times are school time (APP_TIMEZONE), not the server's clock.

def _now_time() -> str:
    return school_now().strftime("%H:%M:%S")


def my_today(db: Db, actor: CurrentUser) -> dict:
    today = school_today()
    row = db.one("SELECT * FROM staff_attendance WHERE user_id = %s AND date = %s", (actor.id, today))
    return row or {"in_time": None, "out_time": None, "date": str(today)}


def check_in(db: Db, actor: CurrentUser) -> dict:
    now, today = _now_time(), school_today()
    with db.transaction():
        row = db.one("SELECT id, in_time FROM staff_attendance WHERE user_id = %s AND date = %s FOR UPDATE",
                     (actor.id, today))
        if row and row["in_time"]:
            raise Conflict("Already checked in today")
        if row:
            db.execute("UPDATE staff_attendance SET in_time = %s WHERE id = %s", (now, row["id"]))
        else:
            try:
                db.execute("INSERT INTO staff_attendance (user_id, date, in_time) VALUES (%s, %s, %s)",
                           (actor.id, today, now))
            except DuplicateKey:
                raise Conflict("Already checked in today") from None
    return {"in_time": now}


def check_out(db: Db, actor: CurrentUser) -> dict:
    now, today = _now_time(), school_today()
    with db.transaction():
        row = db.one("SELECT id, in_time FROM staff_attendance WHERE user_id = %s AND date = %s FOR UPDATE",
                     (actor.id, today))
        if not row or not row["in_time"]:
            raise BadRequest("Check in first")
        db.execute("UPDATE staff_attendance SET out_time = %s WHERE id = %s", (now, row["id"]))
    return {"out_time": now}


def my_attendance_history(db: Db, actor: CurrentUser) -> list[dict]:
    return db.all(
        "SELECT * FROM staff_attendance WHERE user_id = %s ORDER BY date DESC LIMIT 60", (actor.id,)
    )


def staff_attendance_by_day(db: Db, day: date | None) -> list[dict]:
    return db.all(
        "SELECT a.*, u.full_name, u.user_type, u.photo_path "
        "FROM staff_attendance a JOIN users u ON u.id = a.user_id "
        "WHERE a.date = %s ORDER BY u.full_name",
        (day or school_today(),),
    )
