"""What's new per section — feeds the mobile app's red "new" dots.

`GET /updates` returns, for every home-screen section the caller can open,
the time of the newest thing in it (created or changed by someone else:
a teacher, the office, a manager). The app remembers per device and account
when each section was last opened and shows a dot where `latest_at` is
newer. Nothing is stored on the server, so opening a screen costs no write
and two phones on the same parent login each keep their own dots.

    {
      "server_time": "2026-10-25T18:50:00+05:30",
      "sections": {
        "homework": {"latest_at": "2026-10-25T10:12:03+05:30"},
        "messages": {"latest_at": "2026-10-24T16:01:00+05:30", "unread": 2},
        "events":   {"latest_at": null},
        ...
      }
    }

Section keys depend on the role (students: homework, notices, messages,
timetable, syllabus, events, attendance, fees, exams, report_cards,
holidays, transport — staff: notices, holidays, leaves, substitutions and,
by role, timetable, salary, fee_claims). Every key is always present for
the role; `latest_at` is null when the section is empty. Times are ISO 8601
with the school's UTC offset (TIMESTAMP columns are read in school time).

One query per call (scalar subqueries on indexed columns), always filtered
by the caller's school. Deletions don't light a dot (nothing new to open).
"""
from __future__ import annotations

from datetime import datetime

from app.core.clock import SCHOOL_TZ, school_now
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser

STUDENT_SECTIONS = ("homework", "notices", "messages", "timetable", "syllabus", "events", "attendance", "fees",
                    "exams", "report_cards", "holidays", "transport")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=SCHOOL_TZ)
    return value.isoformat(timespec="seconds")


def _newest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def for_user(db: Db, user: CurrentUser) -> dict:
    sections = _student(db, user.id) if user.user_type == "student" else _staff(db, user)
    return {"server_time": _iso(school_now()), "sections": sections}


# ------------------------------------------------------------------ students

def _student(db: Db, uid: int) -> dict:
    me = db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s AND org_id = %s",
                (uid, db.org)) or {}
    p = {"uid": uid, "org": db.org, "cls": me.get("class_id"), "sec": me.get("section_id")}
    # Documents for the whole school, the student's class or the student's section.
    doc_scope = ("d.org_id = %(org)s AND (d.class_id IS NULL OR (d.class_id <=> %(cls)s "
                 "AND (d.section_id IS NULL OR d.section_id <=> %(sec)s)))")
    row = db.one(
        "SELECT "
        "(SELECT MAX(h.updated_at) FROM class_homework h "
        "  WHERE h.org_id = %(org)s AND h.class_id <=> %(cls)s AND h.section_id <=> %(sec)s) AS hw_class, "
        "(SELECT MAX(hw.updated_at) FROM homeworks hw WHERE hw.student_id = %(uid)s) AS hw_own, "
        "(SELECT MAX(n.created_at) FROM notices n WHERE n.org_id = %(org)s) AS notices, "
        "(SELECT MAX(m.created_at) FROM message_recipients r JOIN messages m ON m.id = r.message_id "
        "  WHERE r.student_id = %(uid)s AND m.org_id = %(org)s) AS messages, "
        "(SELECT COUNT(*) FROM message_recipients r JOIN messages m ON m.id = r.message_id "
        "  WHERE r.student_id = %(uid)s AND m.org_id = %(org)s AND r.read_at IS NULL) AS messages_unread, "
        f"(SELECT MAX(d.updated_at) FROM school_documents d WHERE d.kind = 'timetable' AND {doc_scope}) AS tt_docs, "
        "(SELECT MAX(ts.updated_at) FROM timetable_slots ts "
        "  WHERE ts.org_id = %(org)s AND ts.section_id <=> %(sec)s) AS tt_slots, "
        f"(SELECT MAX(d.updated_at) FROM school_documents d WHERE d.kind = 'syllabus' AND {doc_scope}) AS syllabus, "
        "(SELECT MAX(e.updated_at) FROM events e WHERE e.org_id = %(org)s) AS events, "
        "(SELECT MAX(a.created_at) FROM attendance a WHERE a.student_id = %(uid)s AND a.org_id = %(org)s) "
        "  AS attendance, "
        "(SELECT MAX(f.created_at) FROM fees f WHERE f.student_id = %(uid)s AND f.org_id = %(org)s) AS fees, "
        "(SELECT MAX(fc.reviewed_at) FROM fee_claims fc WHERE fc.student_id = %(uid)s AND fc.org_id = %(org)s) "
        "  AS fee_claims, "
        "(SELECT MAX(r.updated_at) FROM exam_results r JOIN exams e ON e.id = r.exam_id "
        "  WHERE r.student_id = %(uid)s AND e.org_id = %(org)s AND e.results_published = 1) AS exams, "
        "(SELECT MAX(rc.created_at) FROM report_cards rc WHERE rc.student_id = %(uid)s) AS report_cards, "
        "(SELECT MAX(ho.created_at) FROM holidays ho WHERE ho.org_id = %(org)s) AS holidays, "
        "(SELECT MAX(st.updated_at) FROM student_transport st WHERE st.student_id = %(uid)s "
        "  AND st.org_id = %(org)s) AS transport",
        p,
    )
    latest = {
        "homework": _newest(row["hw_class"], row["hw_own"]),
        "notices": row["notices"],
        "messages": row["messages"],
        "timetable": _newest(row["tt_docs"], row["tt_slots"]),
        "syllabus": row["syllabus"],
        "events": row["events"],
        "attendance": row["attendance"],
        "fees": _newest(row["fees"], row["fee_claims"]),
        "exams": row["exams"],
        "report_cards": row["report_cards"],
        "holidays": row["holidays"],
        "transport": row["transport"],
    }
    sections = {key: {"latest_at": _iso(latest[key])} for key in STUDENT_SECTIONS}
    sections["messages"]["unread"] = int(row["messages_unread"] or 0)
    return sections


# --------------------------------------------------------------------- staff

def staff_sections(role: str) -> tuple[str, ...]:
    """Section keys for a staff role (matches the launcher of that role in the app)."""
    if role in MANAGERS:
        return ("notices", "holidays", "leaves", "substitutions", "fee_claims")
    if role == "teacher":
        return ("notices", "holidays", "leaves", "substitutions", "timetable", "salary")
    return ("notices", "holidays", "leaves", "salary")  # driver


def _staff(db: Db, user: CurrentUser) -> dict:
    keys = staff_sections(user.user_type)
    manager = user.user_type in MANAGERS
    p = {"uid": user.id, "org": db.org}
    row = db.one(
        "SELECT "
        # someone else's notice (your own post isn't news to you)
        "(SELECT MAX(n.created_at) FROM notices n WHERE n.org_id = %(org)s AND n.created_by <> %(uid)s) "
        "  AS notices, "
        "(SELECT MAX(ho.created_at) FROM holidays ho WHERE ho.org_id = %(org)s) AS holidays, "
        # managers: someone asked for leave · everyone else: your request was answered
        + ("(SELECT MAX(l.created_at) FROM leave_requests l WHERE l.org_id = %(org)s AND l.user_id <> %(uid)s) "
           if manager else
           "(SELECT MAX(l.reviewed_at) FROM leave_requests l WHERE l.org_id = %(org)s AND l.user_id = %(uid)s) ")
        + "  AS leaves, "
        "(SELECT MAX(s.updated_at) FROM substitution_requests s WHERE s.org_id = %(org)s "
        "  AND (s.substitute_id = %(uid)s OR s.requester_id = %(uid)s)) AS substitutions, "
        "(SELECT MAX(ts.updated_at) FROM timetable_slots ts WHERE ts.org_id = %(org)s AND ts.teacher_id = %(uid)s) "
        "  AS timetable, "
        "(SELECT MAX(sr.created_at) FROM salary_records sr WHERE sr.org_id = %(org)s AND sr.user_id = %(uid)s) "
        "  AS salary, "
        "(SELECT MAX(fc.created_at) FROM fee_claims fc WHERE fc.org_id = %(org)s AND fc.status = 'pending') "
        "  AS fee_claims",
        p,
    )
    return {key: {"latest_at": _iso(row[key])} for key in keys}
