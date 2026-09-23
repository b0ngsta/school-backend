"""Role-aware dashboard stats, weekly attendance series, activity feed."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS, STAFF

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

staff = require_roles(*STAFF)
manager = require_roles(*MANAGERS)


def _count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    return list(cur.fetchone().values())[0]


@router.get("/attendance-week")
def attendance_week(user: dict = Depends(staff)):
    """Last 7 days school-wide attendance percentage."""
    out = []
    with db_cursor() as cur:
        for i in range(6, -1, -1):
            d = date.today() - timedelta(days=i)
            cur.execute(
                "SELECT SUM(status='present') AS present, COUNT(*) AS marked "
                "FROM attendance WHERE date = %s",
                (d,),
            )
            row = cur.fetchone()
            marked = int(row["marked"] or 0)
            out.append({
                "date": str(d),
                "day": d.strftime("%a"),
                "percentage": round(int(row["present"] or 0) / marked * 100, 1) if marked else None,
                "present": int(row["present"] or 0),
            })
    return out


@router.get("/activities")
def activities(actor: dict = Depends(manager)):
    """Latest events across modules (newest first)."""
    sql = """
    (SELECT 'admission' AS type, CONCAT('New student admission: ', u.full_name) AS text,
            CONCAT(COALESCE(c.name,''), CASE WHEN s.name IS NULL THEN '' ELSE CONCAT(' — Section ', s.name) END) AS sub,
            u.created_at AS at
       FROM users u
       LEFT JOIN student_profiles sp ON sp.user_id = u.id
       LEFT JOIN classes c ON c.id = sp.class_id
       LEFT JOIN sections s ON s.id = sp.section_id
      WHERE u.user_type = 'student')
    UNION ALL
    (SELECT 'payment', CONCAT('Fee payment received: ₹', FORMAT(t.amount, 0)),
            COALESCE(CONCAT('By ', u.full_name), t.reference), t.created_at
       FROM transactions t LEFT JOIN users u ON u.id = t.student_id
      WHERE t.status = 'success')
    UNION ALL
    (SELECT 'sms', 'Bulk SMS sent', CONCAT(m.recipient_group, COALESCE(CONCAT(' · ', m.template), '')), m.created_at
       FROM sms_messages m)
    UNION ALL
    (SELECT 'lesson', CONCAT('Lesson plan added: ', lp.heading),
            CONCAT(u.full_name, ' · ', c.name), lp.created_at
       FROM lesson_plans lp
       JOIN users u ON u.id = lp.teacher_id
       JOIN classes c ON c.id = lp.class_id)
    UNION ALL
    (SELECT 'enquiry', CONCAT('New enquiry: ', e.student_name),
            CONCAT(COALESCE(e.class_interested, ''), ' · ', e.parent_name), e.created_at
       FROM enquiries e)
    UNION ALL
    (SELECT 'notice', CONCAT('Notice: ', n.title), u.full_name, n.created_at
       FROM notices n JOIN users u ON u.id = n.created_by)
    ORDER BY at DESC LIMIT 10
    """
    with db_cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


@router.get("/stats")
def stats(user: dict = Depends(staff)):
    with db_cursor() as cur:
        if user["user_type"] == "teacher":
            return {
                "role": "teacher",
                "my_classes": _count(
                    cur,
                    "SELECT COUNT(*) FROM teacher_assignments WHERE teacher_id = %s",
                    (user["id"],),
                ),
                "my_students": _count(
                    cur,
                    "SELECT COUNT(*) FROM student_profiles sp "
                    "JOIN teacher_assignments ta ON ta.section_id = sp.section_id "
                    "WHERE ta.teacher_id = %s AND ta.role = 'class_teacher'",
                    (user["id"],),
                ),
                "my_lesson_plans": _count(
                    cur,
                    "SELECT COUNT(*) FROM lesson_plans WHERE teacher_id = %s",
                    (user["id"],),
                ),
                "notices": _count(cur, "SELECT COUNT(*) FROM notices"),
            }
        cur.execute(
            "SELECT SUM(status='present') AS present, COUNT(*) AS marked "
            "FROM attendance WHERE date = CURDATE()"
        )
        att = cur.fetchone()
        marked = int(att["marked"] or 0)
        return {
            "role": user["user_type"],
            "students": _count(
                cur, "SELECT COUNT(*) FROM users WHERE user_type = 'student' AND is_active = 1"
            ),
            "teachers": _count(
                cur, "SELECT COUNT(*) FROM users WHERE user_type = 'teacher' AND is_active = 1"
            ),
            "classes": _count(cur, "SELECT COUNT(*) FROM classes"),
            "sections": _count(cur, "SELECT COUNT(*) FROM sections"),
            "lesson_plans": _count(cur, "SELECT COUNT(*) FROM lesson_plans"),
            "notices": _count(cur, "SELECT COUNT(*) FROM notices"),
            "attendance_today": (
                round(int(att["present"] or 0) / marked * 100, 1) if marked else None
            ),
            "fees_collected": float(
                _count(
                    cur,
                    "SELECT COALESCE(SUM(amount), 0) FROM fees WHERE status = 'paid'",
                )
            ),
            "enquiries_today": _count(
                cur, "SELECT COUNT(*) FROM enquiries WHERE DATE(created_at) = CURDATE()"
            ),
        }
