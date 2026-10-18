"""Weekly timetable — managers assign teacher→class/section/subject per
day+period; teachers view their own; students see their section's timetable
(student_portal). day: 0=Mon … 6=Sun · period: 1..12"""
from __future__ import annotations

from app.core.errors import Forbidden, NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.timetable import SlotUpsert

SLOT_SELECT = (
    "SELECT t.*, u.full_name AS teacher_name, c.name AS class_name, s.name AS section_name "
    "FROM timetable_slots t "
    "JOIN users u ON u.id = t.teacher_id "
    "JOIN classes c ON c.id = t.class_id "
    "JOIN sections s ON s.id = t.section_id"
)


def my_timetable(db: Db, user: CurrentUser) -> list[dict]:
    return db.all(SLOT_SELECT + " WHERE t.teacher_id = %s ORDER BY t.day, t.period", (user.id,))


def list_timetable(db: Db, user: CurrentUser, teacher_id: int | None) -> list[dict]:
    """Managers: any teacher (or all). Teachers: only their own."""
    if user.user_type == "teacher":
        teacher_id = user.id
    elif user.user_type not in MANAGERS:
        raise Forbidden("Not allowed")
    sql, params = SLOT_SELECT, ()
    if teacher_id is not None:
        sql += " WHERE t.teacher_id = %s"
        params = (teacher_id,)
    return db.all(sql + " ORDER BY t.day, t.period", params)


def upsert_slot(db: Db, body: SlotUpsert) -> dict:
    """Create or replace the slot at (teacher, day, period)."""
    with db.transaction():
        if not db.one("SELECT id FROM users WHERE id = %s AND user_type = 'teacher'", (body.teacher_id,)):
            raise NotFound("Teacher not found")
        if not db.one("SELECT id FROM sections WHERE id = %s AND class_id = %s",
                      (body.section_id, body.class_id)):
            raise NotFound("Section not in that class")
        subject = (body.subject or "").strip()[:100] or None
        db.execute(
            "INSERT INTO timetable_slots (teacher_id, day, period, class_id, section_id, subject) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE class_id = VALUES(class_id), section_id = VALUES(section_id), "
            "subject = VALUES(subject)",
            (body.teacher_id, body.day, body.period, body.class_id, body.section_id, subject),
        )
        return db.one(
            SLOT_SELECT + " WHERE t.teacher_id = %s AND t.day = %s AND t.period = %s",
            (body.teacher_id, body.day, body.period),
        )


def delete_slot(db: Db, slot_id: int) -> dict:
    if db.execute("DELETE FROM timetable_slots WHERE id = %s", (slot_id,)).rowcount == 0:
        raise NotFound("Slot not found")
    return {"deleted": slot_id}
