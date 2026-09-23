"""Attendance — mark per section per day.

view: managers + teachers assigned to the section
mark: managers + the section's CLASS teacher
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import STAFF, ensure_can_edit_students, ensure_can_view_section

router = APIRouter(prefix="/attendance", tags=["attendance"])

staff = require_roles(*STAFF)


class AttendanceRecord(BaseModel):
    student_id: int
    status: Literal["present", "absent", "leave"]


class AttendanceMark(BaseModel):
    section_id: int
    date: date
    records: list[AttendanceRecord]


@router.get("/stats")
def attendance_stats(day: date | None = None, user: dict = Depends(staff)):
    """School-wide numbers for a day (default today)."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT "
            "SUM(status='present') AS present, "
            "SUM(status='absent') AS absent, "
            "SUM(status='leave') AS on_leave, "
            "COUNT(*) AS marked "
            "FROM attendance WHERE date = %s",
            (day or date.today(),),
        )
        row = cur.fetchone()
    marked = int(row["marked"] or 0)
    present = int(row["present"] or 0)
    return {
        "present": present,
        "absent": int(row["absent"] or 0),
        "on_leave": int(row["on_leave"] or 0),
        "marked": marked,
        "percentage": round(present / marked * 100, 1) if marked else 0.0,
    }


@router.get("")
def get_attendance(section_id: int, day: date, user: dict = Depends(staff)):
    """Students of a section with their status for the day (null = unmarked)."""
    with db_cursor() as cur:
        cur.execute("SELECT class_id FROM sections WHERE id = %s", (section_id,))
        sec = cur.fetchone()
        if not sec:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found")
        ensure_can_view_section(cur, user, sec["class_id"], section_id)
        cur.execute(
            "SELECT u.id AS student_id, u.full_name, sp.roll_no, a.status "
            "FROM student_profiles sp "
            "JOIN users u ON u.id = sp.user_id "
            "LEFT JOIN attendance a ON a.student_id = u.id AND a.date = %s "
            "WHERE sp.section_id = %s AND u.is_active = 1 "
            "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name",
            (day, section_id),
        )
        return cur.fetchall()


@router.post("")
def mark_attendance(body: AttendanceMark, user: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute("SELECT class_id FROM sections WHERE id = %s", (body.section_id,))
        sec = cur.fetchone()
        if not sec:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found")
        ensure_can_edit_students(cur, user, sec["class_id"], body.section_id)
        for r in body.records:
            cur.execute(
                "INSERT INTO attendance (student_id, section_id, date, status, marked_by) "
                "VALUES (%s, %s, %s, %s, %s) AS new "
                "ON DUPLICATE KEY UPDATE status = new.status, marked_by = new.marked_by",
                (r.student_id, body.section_id, body.date, r.status, user["id"]),
            )
    return {"marked": len(body.records), "date": str(body.date)}
