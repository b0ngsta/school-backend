"""Student-facing data for the mobile app.

Everything is scoped to the logged-in student: no IDs are accepted from the
client, so a student can only ever see their own data.
"""
from __future__ import annotations

from app.db import Db
from app.services import homework as class_homework
from app.services import pagination
from app.services.calendar import get_calendar_payload
from app.services.hr import list_holidays


def profile(db: Db, uid: int) -> dict | None:
    return db.one(
        "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.photo_path, "
        "  sp.class_id, sp.section_id, sp.roll_no, sp.admission_no, sp.dob, "
        "  sp.father_name, sp.mother_name, sp.guardian_phone, sp.address, "
        "  c.name AS class_name, s.name AS section_name "
        "FROM users u "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN classes c ON c.id = sp.class_id "
        "LEFT JOIN sections s ON s.id = sp.section_id "
        "WHERE u.id = %s",
        (uid,),
    )


def _attendance_summary(db: Db, uid: int) -> dict:
    row = db.one(
        "SELECT COUNT(*) AS total, "
        "  SUM(status = 'present') AS present, "
        "  SUM(status = 'absent')  AS absent, "
        "  SUM(status = 'leave')   AS on_leave "
        "FROM attendance WHERE student_id = %s",
        (uid,),
    )
    total = row["total"] or 0
    present = int(row["present"] or 0)
    return {
        "total": total,
        "present": present,
        "absent": int(row["absent"] or 0),
        "on_leave": int(row["on_leave"] or 0),
        "percent": round(present * 100 / total, 1) if total else None,
    }


def dashboard(db: Db, uid: int) -> dict:
    """Everything on the student home screen in four round trips (was six)."""
    me = profile(db, uid)
    counts = db.one(
        "SELECT "
        "(SELECT COUNT(*) FROM homeworks WHERE student_id = %(uid)s AND status = 'pending') "
        "+ (SELECT COUNT(*) FROM class_homework h JOIN student_profiles sp ON sp.section_id = h.section_id "
        "   AND sp.class_id = h.class_id AND sp.user_id = %(uid)s "
        "   LEFT JOIN homework_submissions hs ON hs.homework_id = h.id AND hs.student_id = %(uid)s "
        "   WHERE COALESCE(hs.status, 'pending') = 'pending') AS pending_homework, "
        "(SELECT COUNT(*) FROM fees WHERE student_id = %(uid)s AND status = 'pending') AS fees_n, "
        "(SELECT COALESCE(SUM(amount), 0) FROM fees WHERE student_id = %(uid)s AND status = 'pending') AS fees_due, "
        "(SELECT COUNT(*) FROM attendance WHERE student_id = %(uid)s) AS att_total, "
        "(SELECT SUM(status = 'present') FROM attendance WHERE student_id = %(uid)s) AS att_present, "
        "(SELECT SUM(status = 'absent') FROM attendance WHERE student_id = %(uid)s) AS att_absent, "
        "(SELECT SUM(status = 'leave') FROM attendance WHERE student_id = %(uid)s) AS att_leave",
        {"uid": uid},
    )
    total, present = counts["att_total"] or 0, int(counts["att_present"] or 0)
    attendance_summary = {
        "total": total,
        "present": present,
        "absent": int(counts["att_absent"] or 0),
        "on_leave": int(counts["att_leave"] or 0),
        "percent": round(present * 100 / total, 1) if total else None,
    }

    next_exam = None
    if me and me["class_id"]:
        next_exam = db.one(
            "SELECT id, name, start_date, end_date FROM exams "
            "WHERE (class_id = %s OR class_id IS NULL) AND end_date >= CURDATE() "
            "ORDER BY start_date LIMIT 1",
            (me["class_id"],),
        )
    notices = db.all(
        "SELECT n.id, n.title, n.created_at, u.full_name AS posted_by "
        "FROM notices n JOIN users u ON u.id = n.created_by "
        "ORDER BY n.created_at DESC LIMIT 3"
    )
    return {
        "profile": me,
        "pending_homework": counts["pending_homework"],
        "attendance": attendance_summary,
        "next_exam": next_exam,
        "pending_fees": {"count": counts["fees_n"], "amount_due": float(counts["fees_due"])},
        "latest_notices": notices,
    }


def homework(db: Db, uid: int, page: pagination.Page | None = None) -> pagination.PageResult:
    """Class homework of the student's section merged with per-student rows
    (`scope` "class" | "student"; clients key rows by scope + id)."""
    return pagination.fetch(
        db,
        class_homework.student_homework_sql(
            "hw.id, hw.title, hw.due_date, hw.status, hw.note, hw.created_at, hw.created_by_name AS assigned_by, "
            "hw.subject, hw.scope, hw.homework_id"
        ),
        (uid, uid, uid),
        class_homework.HOMEWORK_ORDER,
        page,
    )


def remarks(db: Db, uid: int) -> list[dict]:
    return db.all(
        "SELECT r.id, r.remark, r.created_at, u.full_name AS by_name "
        "FROM student_remarks r JOIN users u ON u.id = r.created_by "
        "WHERE r.student_id = %s ORDER BY r.created_at DESC",
        (uid,),
    )


def report_cards(db: Db, uid: int) -> list[dict]:
    return db.all(
        "SELECT rc.id, rc.term, rc.grades, rc.remarks, rc.created_at, "
        "  u.full_name AS issued_by "
        "FROM report_cards rc JOIN users u ON u.id = rc.created_by "
        "WHERE rc.student_id = %s ORDER BY rc.created_at DESC",
        (uid,),
    )


def attendance(db: Db, uid: int) -> dict:
    summary = _attendance_summary(db, uid)
    records = db.all(
        "SELECT date, status FROM attendance WHERE student_id = %s ORDER BY date DESC LIMIT 90",
        (uid,),
    )
    return {"summary": summary, "records": records}


def exams(db: Db, uid: int) -> list[dict]:
    me = profile(db, uid)
    if not me or not me["class_id"]:
        return []
    rows = db.all(
        "SELECT e.id, e.name, e.start_date, e.end_date, e.results_published, "
        "  CASE WHEN CURDATE() < e.start_date THEN 'upcoming' "
        "       WHEN CURDATE() > e.end_date THEN 'completed' "
        "       ELSE 'ongoing' END AS status, "
        "  r.marks, r.grade, r.remarks "
        "FROM exams e "
        "LEFT JOIN exam_results r ON r.exam_id = e.id AND r.student_id = %s "
        "WHERE (e.class_id = %s OR e.class_id IS NULL) ORDER BY e.start_date DESC",
        (uid, me["class_id"]),
    )
    # hide marks until results are published
    for e in rows:
        if not e["results_published"]:
            e["marks"] = e["grade"] = e["remarks"] = None
    return rows


def fees(db: Db, uid: int) -> dict:
    records = db.all(
        "SELECT f.id, f.title, f.amount, f.due_date, f.status, f.paid_date, "
        "f.method, f.created_at, "
        "(SELECT fc.status FROM fee_claims fc WHERE fc.fee_id = f.id "
        " ORDER BY fc.created_at DESC LIMIT 1) AS claim_status, "
        "(SELECT fc.review_note FROM fee_claims fc WHERE fc.fee_id = f.id "
        " ORDER BY fc.created_at DESC LIMIT 1) AS claim_review_note "
        "FROM fees f WHERE f.student_id = %s ORDER BY f.created_at DESC",
        (uid,),
    )
    totals = db.one(
        "SELECT COALESCE(SUM(CASE WHEN status = 'pending' THEN amount END), 0) AS pending, "
        "  COALESCE(SUM(CASE WHEN status = 'paid' THEN amount END), 0) AS paid "
        "FROM fees WHERE student_id = %s",
        (uid,),
    )
    return {
        "records": records,
        "totals": {"pending": float(totals["pending"]), "paid": float(totals["paid"])},
    }


def timetable(db: Db, uid: int) -> list[dict]:
    """The weekly timetable of the student's section (with teacher names)."""
    me = profile(db, uid)
    if not me or not me["section_id"]:
        return []
    return db.all(
        "SELECT t.*, u.full_name AS teacher_name, c.name AS class_name, "
        "s.name AS section_name "
        "FROM timetable_slots t "
        "JOIN users u ON u.id = t.teacher_id "
        "JOIN classes c ON c.id = t.class_id "
        "JOIN sections s ON s.id = t.section_id "
        "WHERE t.section_id = %s ORDER BY t.day, t.period",
        (me["section_id"],),
    )


def holidays(db: Db, year: int | None) -> list[dict]:
    return list_holidays(db, year)


def calendar(db: Db) -> dict:
    return get_calendar_payload(db)


def notices(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(
        db,
        "SELECT n.id, n.title, n.body, n.created_at, u.full_name AS posted_by "
        "FROM notices n JOIN users u ON u.id = n.created_by",
        (),
        "n.created_at DESC, n.id DESC",
        page,
    )
