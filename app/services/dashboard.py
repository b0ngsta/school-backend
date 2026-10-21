"""Role-aware dashboard stats, weekly attendance series, activity feed."""
from __future__ import annotations

from datetime import timedelta

from app.core.clock import school_today
from app.db import Db
from app.models.auth import CurrentUser

ACTIVITIES_SQL = """
(SELECT 'admission' AS type, CONCAT('New student admission: ', u.full_name) AS text,
        CONCAT(COALESCE(c.name,''), CASE WHEN s.name IS NULL THEN '' ELSE CONCAT(' — Section ', s.name) END) AS sub,
        u.created_at AS at
   FROM users u
   LEFT JOIN student_profiles sp ON sp.user_id = u.id
   LEFT JOIN classes c ON c.id = sp.class_id
   LEFT JOIN sections s ON s.id = sp.section_id
  WHERE u.org_id = %(org)s AND u.user_type = 'student')
UNION ALL
(SELECT 'payment', CONCAT('Fee payment received: ₹', FORMAT(t.amount, 0)),
        COALESCE(CONCAT('By ', u.full_name), t.reference), t.created_at
   FROM transactions t LEFT JOIN users u ON u.id = t.student_id
  WHERE t.org_id = %(org)s AND t.status = 'success')
UNION ALL
(SELECT 'sms', 'Bulk SMS sent', CONCAT(m.recipient_group, COALESCE(CONCAT(' · ', m.template), '')), m.created_at
   FROM sms_messages m WHERE m.org_id = %(org)s)
UNION ALL
(SELECT 'lesson', CONCAT('Lesson plan added: ', lp.heading),
        CONCAT(u.full_name, ' · ', c.name), lp.created_at
   FROM lesson_plans lp
   JOIN users u ON u.id = lp.teacher_id
   JOIN classes c ON c.id = lp.class_id
  WHERE lp.org_id = %(org)s)
UNION ALL
(SELECT 'enquiry', CONCAT('New enquiry: ', e.student_name),
        CONCAT(COALESCE(e.class_interested, ''), ' · ', e.parent_name), e.created_at
   FROM enquiries e WHERE e.org_id = %(org)s)
UNION ALL
(SELECT 'notice', CONCAT('Notice: ', n.title), u.full_name, n.created_at
   FROM notices n JOIN users u ON u.id = n.created_by WHERE n.org_id = %(org)s)
ORDER BY at DESC LIMIT 10
"""


def attendance_week(db: Db) -> list[dict]:
    """Last 7 days school-wide attendance percentage (one query)."""
    today = school_today()
    first = today - timedelta(days=6)
    rows = db.all(
        "SELECT date, SUM(status='present') AS present, COUNT(*) AS marked "
        "FROM attendance WHERE org_id = %s AND date BETWEEN %s AND %s GROUP BY date",
        (db.org, first, today),
    )
    by_day = {r["date"]: r for r in rows}
    out = []
    for i in range(7):
        d = first + timedelta(days=i)
        row = by_day.get(d, {})
        marked, present = int(row.get("marked") or 0), int(row.get("present") or 0)
        out.append({
            "date": str(d),
            "day": d.strftime("%a"),
            "percentage": round(present / marked * 100, 1) if marked else None,
            "present": present,
        })
    return out


def activities(db: Db) -> list[dict]:
    """Latest events across modules (newest first)."""
    return db.all(ACTIVITIES_SQL, {"org": db.org})


def stats(db: Db, user: CurrentUser) -> dict:
    """Role-aware counters, each role in a single round trip."""
    if user.user_type == "teacher":
        row = db.one(
            "SELECT "
            "(SELECT COUNT(*) FROM teacher_assignments WHERE org_id = %(org)s AND teacher_id = %(uid)s) AS my_classes, "
            "(SELECT COUNT(*) FROM student_profiles sp "
            " JOIN teacher_assignments ta ON ta.section_id = sp.section_id "
            " WHERE ta.org_id = %(org)s AND ta.teacher_id = %(uid)s AND ta.role = 'class_teacher') AS my_students, "
            "(SELECT COUNT(*) FROM lesson_plans WHERE org_id = %(org)s AND teacher_id = %(uid)s) AS my_lesson_plans, "
            "(SELECT COUNT(*) FROM notices WHERE org_id = %(org)s) AS notices",
            {"uid": user.id, "org": db.org},
        )
        return {"role": "teacher", **row}

    row = db.one(
        "SELECT "
        "(SELECT COUNT(*) FROM users WHERE org_id = %(org)s AND user_type = 'student' AND is_active = 1) AS students, "
        "(SELECT COUNT(*) FROM users WHERE org_id = %(org)s AND user_type = 'teacher' AND is_active = 1) AS teachers, "
        "(SELECT COUNT(*) FROM classes WHERE org_id = %(org)s) AS classes, "
        "(SELECT COUNT(*) FROM sections WHERE org_id = %(org)s) AS sections, "
        "(SELECT COUNT(*) FROM lesson_plans WHERE org_id = %(org)s) AS lesson_plans, "
        "(SELECT COUNT(*) FROM notices WHERE org_id = %(org)s) AS notices, "
        "(SELECT SUM(status = 'present') FROM attendance "
        " WHERE org_id = %(org)s AND date = CURDATE()) AS present_today, "
        "(SELECT COUNT(*) FROM attendance WHERE org_id = %(org)s AND date = CURDATE()) AS marked_today, "
        "(SELECT COALESCE(SUM(amount), 0) FROM fees WHERE org_id = %(org)s AND status = 'paid') AS fees_collected, "
        "(SELECT COUNT(*) FROM enquiries WHERE org_id = %(org)s "
        " AND created_at >= CURDATE() AND created_at < CURDATE() + INTERVAL 1 DAY) AS enquiries_today",
        {"org": db.org},
    )
    marked = int(row["marked_today"] or 0)
    return {
        "role": user.user_type,
        "students": row["students"],
        "teachers": row["teachers"],
        "classes": row["classes"],
        "sections": row["sections"],
        "lesson_plans": row["lesson_plans"],
        "notices": row["notices"],
        "attendance_today": round(int(row["present_today"] or 0) / marked * 100, 1) if marked else None,
        "fees_collected": float(row["fees_collected"]),
        "enquiries_today": row["enquiries_today"],
    }
