"""Weekly timetable — managers assign teacher→class/section/subject per
day+period; teachers view their own; students see their section's timetable
(student_portal). day: 0=Mon … 6=Sun · period: 1..12"""
from __future__ import annotations

from datetime import date, timedelta

from app.core.clock import school_today
from app.core.errors import Forbidden, NotFound, Unprocessable
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.timetable import SlotUpsert
from app.services import school_timings

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
        limit = school_timings.periods_per_day(db)
        if limit is not None and body.period > limit:
            raise Unprocessable(f"The school day has {limit} periods (see school timings)")
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


# ------------------------------------------------------------ dated week

SCHOOL_DAYS = 6  # Monday (0) … Saturday (5)


def week(db: Db, user: CurrentUser, start: date | None, teacher_id: int | None) -> dict:
    """The teacher's weekly template for the Monday–Saturday week containing
    `start`, with holidays, their leave days and the periods they cover for
    others (covering) or others cover for them (covered)."""
    if user.user_type not in MANAGERS or teacher_id is None:
        teacher_id = user.id
    monday = (start or school_today())
    monday -= timedelta(days=monday.weekday())
    saturday = monday + timedelta(days=SCHOOL_DAYS - 1)

    holidays = {r["date"]: r["name"] for r in db.all(
        "SELECT date, name FROM holidays WHERE date BETWEEN %s AND %s", (monday, saturday))}
    leaves = db.all(
        "SELECT from_date, to_date FROM leave_requests WHERE user_id = %s AND status IN ('pending', 'approved') "
        "AND from_date <= %s AND to_date >= %s",
        (teacher_id, saturday, monday),
    )
    days = []
    for offset in range(SCHOOL_DAYS):
        d = monday + timedelta(days=offset)
        days.append({
            "date": d, "day": offset, "holiday": holidays.get(d),
            "on_leave": any(lv["from_date"] <= d <= lv["to_date"] for lv in leaves),
        })

    rows = db.all(
        "SELECT r.id, r.date, r.period, r.status, r.requester_id, r.subject, r.updated_at, "
        "c.name AS class_name, s.name AS section_name, "
        "req.full_name AS requester_name, sub.full_name AS substitute_name "
        "FROM substitution_requests r "
        "JOIN classes c ON c.id = r.class_id JOIN sections s ON s.id = r.section_id "
        "JOIN users req ON req.id = r.requester_id JOIN users sub ON sub.id = r.substitute_id "
        "WHERE (r.substitute_id = %s OR r.requester_id = %s) AND r.date BETWEEN %s AND %s "
        "AND r.status IN ('pending', 'accepted') "
        "ORDER BY r.date, r.period, r.status = 'accepted' DESC, r.updated_at DESC, r.id DESC",
        (teacher_id, teacher_id, monday, saturday),
    )
    substitutions, seen = [], set()
    for r in rows:
        kind = "covered" if r["requester_id"] == teacher_id else "covering"
        key = (r["date"], r["period"], kind)
        if key in seen:  # one per day + period: accepted first, then the newest
            continue
        seen.add(key)
        substitutions.append({
            "id": r["id"], "date": r["date"], "day": r["date"].weekday(), "period": r["period"],
            "class_name": r["class_name"], "section_name": r["section_name"], "subject": r["subject"],
            "kind": kind,
            "other_teacher_name": r["substitute_name"] if kind == "covered" else r["requester_name"],
            "status": r["status"],
        })

    slots = db.all(SLOT_SELECT + " WHERE t.teacher_id = %s ORDER BY t.day, t.period", (teacher_id,))
    return {"start": monday, "end": saturday, "days": days, "slots": slots, "substitutions": substitutions}
