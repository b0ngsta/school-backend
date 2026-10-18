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
from app.services import access, audit


def stats(db: Db, day: date | None) -> dict:
    """School-wide numbers for a day (default today)."""
    row = db.one(
        "SELECT "
        "SUM(status='present') AS present, "
        "SUM(status='absent') AS absent, "
        "SUM(status='leave') AS on_leave, "
        "COUNT(*) AS marked "
        "FROM attendance WHERE date = %s",
        (day or school_today(),),
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
    sec = db.one("SELECT class_id FROM sections WHERE id = %s", (section_id,))
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
        "WHERE sp.section_id = %s AND u.is_active = 1 "
        "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name",
        (day, section_id),
    )


def mark_attendance(db: Db, user: CurrentUser, body: AttendanceMark) -> dict:
    with db.transaction():
        access.ensure_can_edit_students(db, user, _section_class(db, body.section_id), body.section_id)
        access.ensure_students_in_section(db, body.section_id, (r.student_id for r in body.records))
        db.execute_many(
            "INSERT INTO attendance (student_id, section_id, date, status, marked_by) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE status = VALUES(status), marked_by = VALUES(marked_by)",
            [(r.student_id, body.section_id, body.date, r.status, user.id) for r in body.records],
        )
        audit.record(db, user, "attendance.mark", "section", body.section_id,
                     {"date": body.date, "records": len(body.records)})
    return {"marked": len(body.records), "date": str(body.date)}
