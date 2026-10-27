"""HR for staff (teachers, managers, drivers): holidays, leave requests,
salary statements and daily check-in / check-out."""
from __future__ import annotations

from datetime import date, timedelta

from app.core.clock import school_now, school_today
from app.core.errors import BadRequest, Conflict, DuplicateKey, Forbidden, NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.hr import HolidayCreate, LeaveCreate, LeaveUpdate, SalaryCreate
from app.services import audit, notify, pagination, substitutions

# ====================== holidays ======================


def list_holidays(db: Db, year: int | None) -> list[dict]:
    sql = (
        "SELECT h.*, u.full_name AS created_by_name FROM holidays h "
        "LEFT JOIN users u ON u.id = h.created_by WHERE h.org_id = %s"
    )
    params: tuple = (db.org,)
    if year:
        sql += " AND YEAR(h.date) = %s"
        params = (db.org, year)
    return db.all(sql + " ORDER BY h.date", params)


def create_holiday(db: Db, actor: CurrentUser, body: HolidayCreate) -> dict:
    if db.one("SELECT id FROM holidays WHERE org_id = %s AND date = %s", (db.org, body.date)):
        raise Conflict("A holiday already exists on that date")
    try:
        holiday_id = db.execute(
            "INSERT INTO holidays (org_id, date, name, description, created_by) VALUES (%s, %s, %s, %s, %s)",
            (db.org, body.date, body.name, body.description, actor.id),
        ).lastrowid
    except DuplicateKey:
        raise Conflict("A holiday already exists on that date") from None
    return {"id": holiday_id}


def update_holiday(db: Db, holiday_id: int, body: HolidayCreate) -> dict:
    if db.one("SELECT id FROM holidays WHERE org_id = %s AND date = %s AND id != %s",
              (db.org, body.date, holiday_id)):
        raise Conflict("A holiday already exists on that date")
    try:
        changed = db.execute(
            "UPDATE holidays SET date=%s, name=%s, description=%s WHERE id=%s AND org_id=%s",
            (body.date, body.name, body.description, holiday_id, db.org),
        ).rowcount
    except DuplicateKey:
        raise Conflict("A holiday already exists on that date") from None
    if changed == 0 and not db.one("SELECT id FROM holidays WHERE id = %s AND org_id = %s", (holiday_id, db.org)):
        raise NotFound("Holiday not found")
    return {"updated": holiday_id}


def delete_holiday(db: Db, holiday_id: int) -> dict:
    if db.execute("DELETE FROM holidays WHERE id = %s AND org_id = %s", (holiday_id, db.org)).rowcount == 0:
        raise NotFound("Holiday not found")
    return {"deleted": holiday_id}


# ====================== leave requests ======================

LEAVE_SELECT = (
    "SELECT l.*, u.full_name, u.user_type, u.photo_path, r.full_name AS reviewed_by_name "
    "FROM leave_requests l "
    "JOIN users u ON u.id = l.user_id "
    "LEFT JOIN users r ON r.id = l.reviewed_by"
)


def _leave_dates(leave: dict) -> str:
    return notify.date_range(leave["from_date"], leave["to_date"])


def request_leave(db: Db, actor: CurrentUser, body: LeaveCreate) -> dict:
    with db.transaction():
        # lock the requester's row so two quick submits can't both pass the check
        db.one("SELECT id FROM users WHERE id = %s FOR UPDATE", (actor.id,))
        if db.one("SELECT id FROM leave_requests WHERE user_id = %s AND status = 'pending'", (actor.id,)):
            raise Conflict("You already have a pending leave request")
        leave_id = db.execute(
            "INSERT INTO leave_requests (org_id, user_id, from_date, to_date, reason) VALUES (%s, %s, %s, %s, %s)",
            (db.org, actor.id, body.from_date, body.to_date, body.reason),
        ).lastrowid
        requester = {"id": actor.id, "full_name": actor.full_name}
        substitutions.create_many(db, requester, body.substitutions, leave_id)
        substitutions.link_standalone(db, actor.id, leave_id, body.from_date, body.to_date)
        notify.notify(db, notify.manager_ids(db, exclude=actor.id), "leave",
                      f"{actor.full_name} applied for leave {_leave_dates(body.model_dump())}", body.reason,
                      data=notify.route("leave", student=False))
    return {"id": leave_id, "status": "pending"}


def my_leaves(db: Db, actor: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    result = pagination.fetch(db, LEAVE_SELECT + " WHERE l.org_id = %s AND l.user_id = %s", (db.org, actor.id),
                              "l.created_at DESC, l.id DESC", page)
    substitutions.coverage(db, result.rows)
    return result


def list_leaves(db: Db, status_filter: str | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql, params = LEAVE_SELECT + " WHERE l.org_id = %s", (db.org,)
    if status_filter:
        sql += " AND l.status = %s"
        params = (db.org, status_filter)
    result = pagination.fetch(db, sql, params, "l.status = 'pending' DESC, l.created_at DESC, l.id DESC", page)
    substitutions.coverage(db, result.rows)
    return result


def _visible_leave(db: Db, actor: CurrentUser, leave_id: int) -> dict:
    leave = db.one(LEAVE_SELECT + " WHERE l.id = %s AND l.org_id = %s", (leave_id, db.org))
    if not leave or (leave["user_id"] != actor.id and actor.user_type not in MANAGERS):
        raise NotFound("Leave request not found")
    return leave


def get_leave(db: Db, actor: CurrentUser, leave_id: int) -> dict:
    leave = _visible_leave(db, actor, leave_id)
    substitutions.coverage(db, [leave])
    leave["substitutions"] = substitutions.for_leave(db, leave_id)
    return leave


def update_leave(db: Db, actor: CurrentUser, leave_id: int, body: LeaveUpdate) -> dict:
    with db.transaction():
        _visible_leave(db, actor, leave_id)
        leave = db.one("SELECT * FROM leave_requests WHERE id = %s FOR UPDATE", (leave_id,))
        if leave["user_id"] != actor.id:
            raise Forbidden("Only the applicant can edit a leave request")
        if leave["status"] != "pending":
            raise Conflict("Only pending requests can be edited")
        db.execute("UPDATE leave_requests SET from_date = %s, to_date = %s, reason = %s WHERE id = %s",
                   (body.from_date, body.to_date, body.reason, leave_id))
        substitutions.cancel_for_leave(db, leave_id, keep_from=body.from_date, keep_to=body.to_date)
        requester = {"id": actor.id, "full_name": actor.full_name}
        substitutions.create_many(db, requester, body.substitutions, leave_id)
        substitutions.link_standalone(db, actor.id, leave_id, body.from_date, body.to_date)
    return get_leave(db, actor, leave_id)


def delete_leave(db: Db, actor: CurrentUser, leave_id: int) -> None:
    """Owner: a pending request, or an approved leave that hasn't started.
    Managers: any leave that hasn't started. Pending ones are deleted;
    approved ones become 'cancelled' (history is kept)."""
    today = school_today()
    with db.transaction():
        _visible_leave(db, actor, leave_id)
        leave = db.one("SELECT * FROM leave_requests WHERE id = %s FOR UPDATE", (leave_id,))
        is_owner, not_started = leave["user_id"] == actor.id, leave["from_date"] > today
        if leave["status"] not in ("pending", "approved"):
            raise Conflict(f"This leave request is already {leave['status']}")
        allowed = (is_owner and (leave["status"] == "pending" or not_started)) or (
            actor.user_type in MANAGERS and not_started)
        if not allowed:
            raise Conflict("This leave has already started")
        substitutions.cancel_for_leave(db, leave_id)
        if leave["status"] == "pending":
            db.execute("DELETE FROM leave_requests WHERE id = %s", (leave_id,))
        else:
            db.execute("UPDATE leave_requests SET status = 'cancelled' WHERE id = %s", (leave_id,))
            applicant = db.scalar("SELECT full_name FROM users WHERE id = %s", (leave["user_id"],))
            notify.notify(db, notify.manager_ids(db, exclude=actor.id), "leave",
                          f"{applicant}'s approved leave {_leave_dates(leave)} was cancelled",
                          data=notify.route("leave", student=False))
        audit.record(db, actor, "leave.delete" if leave["status"] == "pending" else "leave.cancel",
                     "leave_request", leave_id, {"user_id": leave["user_id"]})


def review_leave(db: Db, actor: CurrentUser, leave_id: int, new_status: str, note: str | None) -> dict:
    with db.transaction():
        leave = db.one("SELECT * FROM leave_requests WHERE id = %s AND org_id = %s FOR UPDATE", (leave_id, db.org))
        if not leave:
            raise NotFound("Leave request not found")
        if leave["status"] != "pending":
            raise Conflict(f"Already {leave['status']}")
        db.execute(
            "UPDATE leave_requests SET status=%s, reviewed_by=%s, reviewed_at=NOW(), review_note=%s "
            "WHERE id=%s AND status='pending'",
            (new_status, actor.id, note, leave_id),
        )
        if new_status == "rejected":
            substitutions.cancel_for_leave(db, leave_id)
        audit.record(db, actor, f"leave.{new_status}", "leave_request", leave_id, {"user_id": leave["user_id"]})
        notify.notify(db, [leave["user_id"]], "leave", f"Your leave ({_leave_dates(leave)}) was {new_status}",
                      note, data=notify.route("leave", student=False))
    return {new_status: leave_id}


# ====================== salaries ======================

def my_salaries(db: Db, actor: CurrentUser) -> list[dict]:
    return db.all(
        "SELECT s.*, u.full_name AS created_by_name FROM salary_records s "
        "LEFT JOIN users u ON u.id = s.created_by "
        "WHERE s.user_id = %s ORDER BY s.year DESC, s.month DESC",
        (actor.id,),
    )


def list_salaries(db: Db, user_id: int | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql = ("SELECT s.*, u.full_name, u.user_type FROM salary_records s JOIN users u ON u.id = s.user_id "
           "WHERE s.org_id = %s")
    params: tuple = (db.org,)
    if user_id is not None:
        sql += " AND s.user_id = %s"
        params = (db.org, user_id)
    return pagination.fetch(db, sql, params, "s.year DESC, s.month DESC, u.full_name, s.id", page)


def save_salary(db: Db, actor: CurrentUser, body: SalaryCreate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM users WHERE id = %s AND org_id = %s AND user_type != 'student'",
                      (body.user_id, db.org)):
            raise NotFound("Staff member not found")
        db.execute(
            "INSERT INTO salary_records (org_id, user_id, month, year, amount, status, note, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE amount = VALUES(amount), status = VALUES(status), note = VALUES(note)",
            (db.org, body.user_id, body.month, body.year, body.amount, body.status, body.note, actor.id),
        )
        audit.record(db, actor, "salary.save", "user", body.user_id, body.model_dump())
    return {"saved": True}


def delete_salary(db: Db, actor: CurrentUser, salary_id: int) -> dict:
    with db.transaction():
        if db.execute("DELETE FROM salary_records WHERE id = %s AND org_id = %s", (salary_id, db.org)).rowcount == 0:
            raise NotFound("Salary record not found")
        audit.record(db, actor, "salary.delete", "salary_record", salary_id)
    return {"deleted": salary_id}


# ====================== staff attendance ======================
# Times are school time (APP_TIMEZONE), not the server's clock.

def _now_time() -> str:
    return school_now().strftime("%H:%M:%S")


def _clock(value) -> str | None:
    """TIME column -> "HH:MM:SS" (PyMySQL returns a timedelta, which JSON would
    otherwise turn into a number of seconds)."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds()) % 86400
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return value.strftime("%H:%M:%S")


def _times(row: dict) -> dict:
    row["in_time"], row["out_time"] = _clock(row.get("in_time")), _clock(row.get("out_time"))
    return row


def my_today(db: Db, actor: CurrentUser) -> dict:
    today = school_today()
    row = db.one("SELECT * FROM staff_attendance WHERE user_id = %s AND date = %s", (actor.id, today))
    return _times(row) if row else {"in_time": None, "out_time": None, "date": str(today)}


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
                db.execute("INSERT INTO staff_attendance (org_id, user_id, date, in_time) VALUES (%s, %s, %s, %s)",
                           (db.org, actor.id, today, now))
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
    return [_times(r) for r in db.all(
        "SELECT * FROM staff_attendance WHERE user_id = %s ORDER BY date DESC LIMIT 60", (actor.id,)
    )]


def staff_attendance_by_day(db: Db, day: date | None, page: pagination.Page | None = None) -> pagination.PageResult:
    result = pagination.fetch(
        db,
        "SELECT a.*, u.full_name, u.user_type, u.photo_path "
        "FROM staff_attendance a JOIN users u ON u.id = a.user_id "
        "WHERE a.org_id = %s AND a.date = %s",
        (db.org, day or school_today()),
        "u.full_name, a.id",
        page,
    )
    return pagination.PageResult([_times(r) for r in result.rows], result.total)
