"""Attendance — marked per section per day.

view: managers + teachers assigned to the section
mark: managers + the section's CLASS teacher
"""
from __future__ import annotations

from datetime import date

from app.core.clock import school_today
from app.core.errors import NotFound
from app.db import Db
from app.models.attendance import AttendanceMark
from app.models.auth import CurrentUser
from app.services import access, audit, notify


def stats(db: Db, day: date | None) -> dict:
    """School-wide numbers for a day (default today)."""
    row = db.one(
        "SELECT "
        "SUM(status='present') AS present, "
        "SUM(status='absent') AS absent, "
        "SUM(status='leave') AS on_leave, "
        "COUNT(*) AS marked "
        "FROM attendance WHERE org_id = %s AND date = %s",
        (db.org, day or school_today()),
    )
    marked, present = int(row["marked"] or 0), int(row["present"] or 0)
    return {
        "present": present,
        "absent": int(row["absent"] or 0),
        "on_leave": int(row["on_leave"] or 0),
        "marked": marked,
        "percentage": round(present / marked * 100, 1) if marked else 0.0,
    }


def _section_class(db: Db, section_id: int) -> int:
    sec = db.one("SELECT class_id FROM sections WHERE id = %s AND org_id = %s", (section_id, db.org))
    if not sec:
        raise NotFound("Section not found")
    return sec["class_id"]


def get_attendance(db: Db, user: CurrentUser, section_id: int, day: date) -> list[dict]:
    """Students of a section with their status for the day (null = unmarked)."""
    access.ensure_can_view_section(db, user, _section_class(db, section_id), section_id)
    return db.all(
        "SELECT u.id AS student_id, u.full_name, sp.roll_no, a.status "
        "FROM student_profiles sp "
        "JOIN users u ON u.id = sp.user_id "
        "LEFT JOIN attendance a ON a.student_id = u.id AND a.date = %s "
        "WHERE sp.org_id = %s AND sp.section_id = %s AND u.is_active = 1 "
        "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name",
        (day, db.org, section_id),
    )


def mark_attendance(db: Db, user: CurrentUser, body: AttendanceMark) -> dict:
    with db.transaction():
        access.ensure_can_edit_students(db, user, _section_class(db, body.section_id), body.section_id)
        access.ensure_students_in_section(db, body.section_id, (r.student_id for r in body.records))
        previous = _statuses(db, body.date, [r.student_id for r in body.records])
        db.execute_many(
            "INSERT INTO attendance (org_id, student_id, section_id, date, status, marked_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE status = VALUES(status), marked_by = VALUES(marked_by)",
            [(db.org, r.student_id, body.section_id, body.date, r.status, user.id) for r in body.records],
        )
        audit.record(db, user, "attendance.mark", "section", body.section_id,
                     {"date": body.date, "records": len(body.records)})
        _notify_parents(db, body, previous)
    return {"marked": len(body.records), "date": str(body.date)}


def _statuses(db: Db, day: date, student_ids: list[int]) -> dict[int, str]:
    if not student_ids:
        return {}
    placeholders = ",".join(["%s"] * len(student_ids))
    rows = db.all(f"SELECT student_id, status FROM attendance WHERE org_id = %s AND date = %s "
                  f"AND student_id IN ({placeholders})", (db.org, day, *student_ids))
    return {r["student_id"]: r["status"] for r in rows}


def _notify_parents(db: Db, body: AttendanceMark, previous: dict[int, str]) -> None:
    """Tell each parent (the student account) their child's status; when a day is
    marked again, only the students whose status changed are notified."""
    changed = {r.student_id: r.status for r in body.records if previous.get(r.student_id) != r.status}
    if not changed:
        return
    placeholders = ",".join(["%s"] * len(changed))
    names = {r["id"]: notify.first_name(r["full_name"]) for r in db.all(
        f"SELECT id, full_name FROM users WHERE org_id = %s AND id IN ({placeholders})", (db.org, *changed))}
    today = body.date == school_today()
    when = "today" if today else f"on {notify.day_month(body.date)}"
    data = notify.route("attendance", student=True)
    items = []
    for student_id, status in changed.items():
        name = names.get(student_id, "Your child")
        if student_id in previous:
            title = f"Attendance updated: {name} is now {'on leave' if status == 'leave' else status}"
            if not today:
                title += f" ({notify.day_month(body.date)})"
        elif status == "present":
            title = f"✅ {name} is present today" if today else f"✅ {name} was present {when}"
        elif status == "absent":
            title = f"⚠️ {name} was marked absent {when}" + (f" ({notify.day_month(body.date)})" if today else "")
        else:
            title = f"📝 {name} is on leave {when}" + (f" ({notify.day_month(body.date)})" if today else "")
        items.append(notify.Item(student_id, title, None, data))
    notify.notify_each(db, items, "attendance")
