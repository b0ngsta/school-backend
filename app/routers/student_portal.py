"""Student-facing endpoints for the mobile app.

Everything here is scoped to the logged-in student ("me") — no IDs are
accepted from the client, so a student can only ever see their own data.
Add new student features to this router.
"""
from fastapi import APIRouter, Depends

from app.database import db_cursor
from app.deps import require_roles
from app.routers.calendar import get_calendar_payload

router = APIRouter(prefix="/student", tags=["student portal"])

me = require_roles("student")


def _profile(cur, uid: int) -> dict:
    cur.execute(
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
    return cur.fetchone()


def _attendance_summary(cur, uid: int) -> dict:
    cur.execute(
        "SELECT COUNT(*) AS total, "
        "  SUM(status = 'present') AS present, "
        "  SUM(status = 'absent')  AS absent, "
        "  SUM(status = 'leave')   AS on_leave "
        "FROM attendance WHERE student_id = %s",
        (uid,),
    )
    row = cur.fetchone()
    total = row["total"] or 0
    present = int(row["present"] or 0)
    return {
        "total": total,
        "present": present,
        "absent": int(row["absent"] or 0),
        "on_leave": int(row["on_leave"] or 0),
        "percent": round(present * 100 / total, 1) if total else None,
    }


@router.get("/me")
def my_profile(user: dict = Depends(me)):
    with db_cursor() as cur:
        return _profile(cur, user["id"])


@router.get("/dashboard")
def my_dashboard(user: dict = Depends(me)):
    uid = user["id"]
    with db_cursor() as cur:
        profile = _profile(cur, uid)

        cur.execute(
            "SELECT COUNT(*) AS n FROM homeworks "
            "WHERE student_id = %s AND status = 'pending'",
            (uid,),
        )
        pending_homework = cur.fetchone()["n"]

        attendance = _attendance_summary(cur, uid)

        next_exam = None
        if profile and profile["class_id"]:
            cur.execute(
                "SELECT id, name, start_date, end_date FROM exams "
                "WHERE (class_id = %s OR class_id IS NULL) AND end_date >= CURDATE() "
                "ORDER BY start_date LIMIT 1",
                (profile["class_id"],),
            )
            next_exam = cur.fetchone()

        cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS due "
            "FROM fees WHERE student_id = %s AND status = 'pending'",
            (uid,),
        )
        fees = cur.fetchone()

        cur.execute(
            "SELECT n.id, n.title, n.created_at, u.full_name AS posted_by "
            "FROM notices n JOIN users u ON u.id = n.created_by "
            "ORDER BY n.created_at DESC LIMIT 3"
        )
        notices = cur.fetchall()

    return {
        "profile": profile,
        "pending_homework": pending_homework,
        "attendance": attendance,
        "next_exam": next_exam,
        "pending_fees": {"count": fees["n"], "amount_due": float(fees["due"])},
        "latest_notices": notices,
    }


@router.get("/homework")
def my_homework(user: dict = Depends(me)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT h.id, h.title, h.due_date, h.status, h.note, h.created_at, "
            "  u.full_name AS assigned_by "
            "FROM homeworks h JOIN users u ON u.id = h.created_by "
            "WHERE h.student_id = %s ORDER BY h.created_at DESC",
            (user["id"],),
        )
        return cur.fetchall()


@router.get("/remarks")
def my_remarks(user: dict = Depends(me)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT r.id, r.remark, r.created_at, u.full_name AS by_name "
            "FROM student_remarks r JOIN users u ON u.id = r.created_by "
            "WHERE r.student_id = %s ORDER BY r.created_at DESC",
            (user["id"],),
        )
        return cur.fetchall()


@router.get("/report-cards")
def my_report_cards(user: dict = Depends(me)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT rc.id, rc.term, rc.grades, rc.remarks, rc.created_at, "
            "  u.full_name AS issued_by "
            "FROM report_cards rc JOIN users u ON u.id = rc.created_by "
            "WHERE rc.student_id = %s ORDER BY rc.created_at DESC",
            (user["id"],),
        )
        return cur.fetchall()


@router.get("/attendance")
def my_attendance(user: dict = Depends(me)):
    with db_cursor() as cur:
        summary = _attendance_summary(cur, user["id"])
        cur.execute(
            "SELECT date, status FROM attendance "
            "WHERE student_id = %s ORDER BY date DESC LIMIT 90",
            (user["id"],),
        )
        records = cur.fetchall()
    return {"summary": summary, "records": records}


@router.get("/exams")
def my_exams(user: dict = Depends(me)):
    uid = user["id"]
    with db_cursor() as cur:
        profile = _profile(cur, uid)
        if not profile or not profile["class_id"]:
            return []
        cur.execute(
            "SELECT e.id, e.name, e.start_date, e.end_date, e.results_published, "
            "  CASE WHEN CURDATE() < e.start_date THEN 'upcoming' "
            "       WHEN CURDATE() > e.end_date THEN 'completed' "
            "       ELSE 'ongoing' END AS status, "
            "  r.marks, r.grade, r.remarks "
            "FROM exams e "
            "LEFT JOIN exam_results r "
            "  ON r.exam_id = e.id AND r.student_id = %s "
            "WHERE (e.class_id = %s OR e.class_id IS NULL) ORDER BY e.start_date DESC",
            (uid, profile["class_id"]),
        )
        exams = cur.fetchall()
    # hide marks until results are published
    for e in exams:
        if not e["results_published"]:
            e["marks"] = e["grade"] = e["remarks"] = None
    return exams


@router.get("/fees")
def my_fees(user: dict = Depends(me)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT f.id, f.title, f.amount, f.due_date, f.status, f.paid_date, "
            "f.method, f.created_at, "
            "(SELECT fc.status FROM fee_claims fc WHERE fc.fee_id = f.id "
            " ORDER BY fc.created_at DESC LIMIT 1) AS claim_status, "
            "(SELECT fc.review_note FROM fee_claims fc WHERE fc.fee_id = f.id "
            " ORDER BY fc.created_at DESC LIMIT 1) AS claim_review_note "
            "FROM fees f WHERE f.student_id = %s ORDER BY f.created_at DESC",
            (user["id"],),
        )
        records = cur.fetchall()
        cur.execute(
            "SELECT COALESCE(SUM(CASE WHEN status = 'pending' THEN amount END), 0) AS pending, "
            "  COALESCE(SUM(CASE WHEN status = 'paid' THEN amount END), 0) AS paid "
            "FROM fees WHERE student_id = %s",
            (user["id"],),
        )
        totals = cur.fetchone()
    return {
        "records": records,
        "totals": {"pending": float(totals["pending"]), "paid": float(totals["paid"])},
    }


@router.get("/timetable")
def my_class_timetable(user: dict = Depends(me)):
    """The weekly timetable of the student's section (with teacher names)."""
    with db_cursor() as cur:
        profile = _profile(cur, user["id"])
        if not profile or not profile["section_id"]:
            return []
        cur.execute(
            "SELECT t.*, u.full_name AS teacher_name, c.name AS class_name, "
            "s.name AS section_name "
            "FROM timetable_slots t "
            "JOIN users u ON u.id = t.teacher_id "
            "JOIN classes c ON c.id = t.class_id "
            "JOIN sections s ON s.id = t.section_id "
            "WHERE t.section_id = %s ORDER BY t.day, t.period",
            (profile["section_id"],),
        )
        return cur.fetchall()


@router.get("/holidays")
def student_holidays(year: int | None = None, user: dict = Depends(me)):
    with db_cursor() as cur:
        if year:
            cur.execute(
                "SELECT h.*, u.full_name AS created_by_name FROM holidays h "
                "JOIN users u ON u.id = h.created_by "
                "WHERE YEAR(h.date) = %s ORDER BY h.date",
                (year,),
            )
        else:
            cur.execute(
                "SELECT h.*, u.full_name AS created_by_name FROM holidays h "
                "JOIN users u ON u.id = h.created_by ORDER BY h.date"
            )
        return cur.fetchall()


@router.get("/calendar")
def student_calendar(user: dict = Depends(me)):
    """School calendar (events + exams + holidays) for the mobile app."""
    with db_cursor() as cur:
        return get_calendar_payload(cur)


@router.get("/notices")
def my_notices(user: dict = Depends(me)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT n.id, n.title, n.body, n.created_at, u.full_name AS posted_by "
            "FROM notices n JOIN users u ON u.id = n.created_by "
            "ORDER BY n.created_at DESC"
        )
        return cur.fetchall()
