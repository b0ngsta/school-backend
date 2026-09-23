"""Weekly timetable — managers assign teacher→class/section/subject per
day+period; teachers view their own; students see their section's timetable
via /student/timetable (student_portal.py).

day: 0=Mon … 6=Sun · period: 1..12
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS, STAFF

router = APIRouter(prefix="/timetable", tags=["timetable"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)

SLOT_SELECT = (
    "SELECT t.*, u.full_name AS teacher_name, c.name AS class_name, s.name AS section_name "
    "FROM timetable_slots t "
    "JOIN users u ON u.id = t.teacher_id "
    "JOIN classes c ON c.id = t.class_id "
    "JOIN sections s ON s.id = t.section_id"
)


class SlotUpsert(BaseModel):
    teacher_id: int
    day: int = Field(ge=0, le=6)
    period: int = Field(ge=1, le=12)
    class_id: int
    section_id: int
    subject: str | None = Field(default=None, max_length=100)


@router.get("/mine")
def my_timetable(user: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            SLOT_SELECT + " WHERE t.teacher_id = %s ORDER BY t.day, t.period",
            (user["id"],),
        )
        return cur.fetchall()


@router.get("")
def list_timetable(teacher_id: int | None = None, user: dict = Depends(staff)):
    """Managers: any teacher (or all). Teachers: only their own."""
    if user["user_type"] == "teacher":
        teacher_id = user["id"]
    elif user["user_type"] not in MANAGERS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
    sql, params = SLOT_SELECT, ()
    if teacher_id is not None:
        sql += " WHERE t.teacher_id = %s"
        params = (teacher_id,)
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY t.day, t.period", params)
        return cur.fetchall()


@router.put("")
def upsert_slot(body: SlotUpsert, actor: dict = Depends(manager)):
    """Create or replace the slot at (teacher, day, period)."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE id = %s AND user_type = 'teacher'",
            (body.teacher_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Teacher not found")
        cur.execute(
            "SELECT id FROM sections WHERE id = %s AND class_id = %s",
            (body.section_id, body.class_id),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not in that class")
        subject = (body.subject or "").strip()[:100] or None
        cur.execute(
            "INSERT INTO timetable_slots (teacher_id, day, period, class_id, section_id, subject) "
            "VALUES (%s, %s, %s, %s, %s, %s) AS new "
            "ON DUPLICATE KEY UPDATE class_id=new.class_id, section_id=new.section_id, "
            "subject=new.subject",
            (body.teacher_id, body.day, body.period, body.class_id, body.section_id, subject),
        )
        cur.execute(
            SLOT_SELECT + " WHERE t.teacher_id = %s AND t.day = %s AND t.period = %s",
            (body.teacher_id, body.day, body.period),
        )
        return cur.fetchone()


@router.delete("/{slot_id}")
def delete_slot(slot_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM timetable_slots WHERE id = %s", (slot_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Slot not found")
    return {"deleted": slot_id}
