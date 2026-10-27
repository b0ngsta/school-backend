"""Student-facing data for the mobile app.

Everything is scoped to the logged-in student: no IDs are accepted from the
client, so a student can only ever see their own data.
"""
from __future__ import annotations

from datetime import date

from app.core.errors import NotFound
from app.db import Db
from app.services import homework as class_homework
from app.services import pagination
from app.services.calendar import get_calendar_payload
from app.services.hr import list_holidays
from app.services.school_profile import session_bounds, session_label


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
        "WHERE u.id = %s AND u.org_id = %s",
        (uid, db.org),
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
    """Everything on the student home screen in five round trips (incl. the school's name,
    logo and current session for the header)."""
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
            "WHERE org_id = %s AND (class_id = %s OR class_id IS NULL) AND end_date >= CURDATE() "
            "ORDER BY start_date LIMIT 1",
            (db.org, me["class_id"]),
        )
    notices = db.all(
        "SELECT n.id, n.title, n.created_at, u.full_name AS posted_by "
        "FROM notices n LEFT JOIN users u ON u.id = n.created_by WHERE n.org_id = %s "
        "ORDER BY n.created_at DESC LIMIT 3",
        (db.org,),
    )
    school = db.one("SELECT name, code, logo_path, session_start_month FROM organisations WHERE id = %s", (db.org,))
    start, end = session_bounds(int(school.pop("session_start_month") or 4))
    school["session"] = session_label(start, end)
    return {
        "profile": me,
        "school": school,
        "pending_homework": counts["pending_homework"],
        "attendance": attendance_summary,
        "next_exam": next_exam,
        "pending_fees": {"count": counts["fees_n"], "amount_due": float(counts["fees_due"])},
        "latest_notices": notices,
    }


def homework(db: Db, uid: int, page: pagination.Page | None = None, given_on: date | None = None,
             subject: str | None = None) -> pagination.PageResult:
    """Class homework of the student's section merged with per-student rows
    (`scope` "class" | "student"; clients key rows by scope + id).
    Optional filters: the day it was given, the subject ("General" = none)."""
    where, params = class_homework.student_homework_filters(given_on, subject)
    return pagination.fetch(
        db,
        class_homework.student_homework_sql(
            "hw.id, hw.title, hw.due_date, hw.status, hw.note, hw.created_at, hw.created_by_name AS assigned_by, "
            "hw.subject, hw.scope, hw.homework_id, hw.attachment_path, hw.attachment_name, hw.attachment_type"
        ) + where,
        (uid, uid, uid, *params),
        class_homework.HOMEWORK_ORDER,
        page,
    )


def homework_subjects(db: Db, uid: int) -> list[dict]:
    return class_homework.subjects_for_student(db, uid)


def remarks(db: Db, uid: int) -> list[dict]:
    return db.all(
        "SELECT r.id, r.remark, r.created_at, u.full_name AS by_name "
        "FROM student_remarks r LEFT JOIN users u ON u.id = r.created_by "
        "WHERE r.student_id = %s ORDER BY r.created_at DESC",
        (uid,),
    )


def report_cards(db: Db, uid: int) -> list[dict]:
    return db.all(
        "SELECT rc.id, rc.term, rc.grades, rc.remarks, rc.created_at, "
        "  u.full_name AS issued_by "
        "FROM report_cards rc LEFT JOIN users u ON u.id = rc.created_by "
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
        "SELECT e.id, e.name, e.start_date, e.end_date, e.results_published, e.max_marks AS exam_max_marks, "
        "  CASE WHEN CURDATE() < e.start_date THEN 'upcoming' "
        "       WHEN CURDATE() > e.end_date THEN 'completed' "
        "       ELSE 'ongoing' END AS status, "
        "  r.marks, r.grade, r.remarks, r.max_marks "
        "FROM exams e "
        "LEFT JOIN exam_results r ON r.exam_id = e.id AND r.student_id = %s AND r.subject = '' "
        "WHERE e.org_id = %s AND (e.class_id = %s OR e.class_id IS NULL) ORDER BY e.start_date DESC",
        (uid, db.org, me["class_id"]),
    )
    published = [e["id"] for e in rows if e["results_published"]]
    by_exam: dict[int, list[dict]] = {}
    if published:
        for r in db.all(
            "SELECT exam_id, subject, marks, max_marks, grade, remarks FROM exam_results "
            f"WHERE student_id = %s AND subject <> '' AND exam_id IN ({','.join(['%s'] * len(published))}) "
            "ORDER BY exam_id, id",
            (uid, *published),
        ):
            by_exam.setdefault(r.pop("exam_id"), []).append(r)
    for e in rows:
        exam_max = e.pop("exam_max_marks")
        if not e["results_published"]:  # hide marks until results are published
            e["marks"] = e["grade"] = e["remarks"] = e["max_marks"] = None
            e["subjects"], e["total"], e["max_total"] = [], None, None
            continue
        subjects = by_exam.get(e["id"], [])
        for sub in subjects:
            if sub["max_marks"] is None:
                sub["max_marks"] = exam_max
        e["subjects"] = subjects
        marked = [sub for sub in subjects if sub["marks"] is not None]
        e["total"] = sum(float(sub["marks"]) for sub in marked) if marked else None
        e["max_total"] = (sum(float(sub["max_marks"]) for sub in marked)
                          if marked and all(sub["max_marks"] is not None for sub in marked) else None)
        if e["max_marks"] is None and e["marks"] is not None:
            e["max_marks"] = exam_max
        if e["marks"] is None and e["total"] is not None:
            # only subject marks were entered: older app builds show `marks`, so give them the total
            e["marks"], e["max_marks"] = e["total"], e["max_total"]
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


_NOTICE_SELECT = (
    "SELECT n.id, n.title, n.body, n.created_at, u.full_name AS posted_by, "
    "n.attachment_path, n.attachment_name, n.attachment_type "
    "FROM notices n LEFT JOIN users u ON u.id = n.created_by WHERE n.org_id = %s"
)


def notices(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(db, _NOTICE_SELECT, (db.org,), "n.created_at DESC, n.id DESC", page)


def notice(db: Db, notice_id: int) -> dict:
    row = db.one(_NOTICE_SELECT + " AND n.id = %s", (db.org, notice_id))
    if not row:
        raise NotFound("Notice not found")
    return row


def teachers(db: Db, uid: int) -> list[dict]:
    """The teachers of the student's section, each once, with the subjects
    they teach it (the class teacher first)."""
    me = profile(db, uid)
    if not me or not me["section_id"]:
        return []
    rows = db.all(
        "SELECT u.id, u.full_name, u.email, u.photo_path, tp.designation, tp.qualification, ta.role, ta.subject "
        "FROM teacher_assignments ta JOIN users u ON u.id = ta.teacher_id AND u.is_active = 1 "
        "LEFT JOIN teacher_profiles tp ON tp.user_id = u.id "
        "WHERE ta.org_id = %s AND ta.section_id = %s ORDER BY u.full_name, ta.id",
        (db.org, me["section_id"]),
    )
    out: dict[int, dict] = {}
    for r in rows:
        t = out.setdefault(r["id"], {k: r[k] for k in ("id", "full_name", "email", "photo_path", "designation",
                                                        "qualification")} | {"subjects": [],
                                                                             "is_class_teacher": False})
        if r["role"] == "class_teacher":
            t["is_class_teacher"] = True
        subject = (r["subject"] or "").strip()
        if subject and subject not in t["subjects"]:
            t["subjects"].append(subject)
    return sorted(out.values(), key=lambda t: (not t["is_class_teacher"], t["full_name"].lower()))
