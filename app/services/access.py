"""Who may see or change which class/section — shared by several services."""
from __future__ import annotations

from collections.abc import Iterable

from app.core.errors import BadRequest, Forbidden
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser


def get_assignments(db: Db, teacher_id: int, class_id: int | None = None,
                    section_id: int | None = None) -> list[dict]:
    sql = (
        "SELECT ta.*, c.name AS class_name, s.name AS section_name, "
        "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.section_id = ta.section_id) AS student_count "
        "FROM teacher_assignments ta "
        "JOIN classes c ON c.id = ta.class_id "
        "JOIN sections s ON s.id = ta.section_id "
        "WHERE ta.org_id = %s AND ta.teacher_id = %s"
    )
    params: list = [db.org, teacher_id]
    if class_id is not None:
        sql += " AND ta.class_id = %s"
        params.append(class_id)
    if section_id is not None:
        sql += " AND ta.section_id = %s"
        params.append(section_id)
    return db.all(sql, params)


def _has_assignment(db: Db, teacher_id: int, class_id: int | None, section_id: int | None,
                    class_teacher_only: bool = False) -> bool:
    # `<=>` (not `=`): a student whose class was deleted has class_id NULL and
    # must not match every teacher's assignments.
    sql = "SELECT 1 FROM teacher_assignments WHERE org_id = %s AND teacher_id = %s AND class_id <=> %s"
    params: list = [db.org, teacher_id, class_id]
    if section_id is not None:
        sql += " AND section_id = %s"
        params.append(section_id)
    if class_teacher_only:
        sql += " AND role = 'class_teacher'"
    return db.one(sql + " LIMIT 1", params) is not None


def is_assigned(db: Db, user: CurrentUser, class_id: int | None, section_id: int | None) -> bool:
    """Teacher has ANY assignment (class or subject) on this class/section."""
    return _has_assignment(db, user.id, class_id, section_id)


def is_class_teacher(db: Db, user: CurrentUser, class_id: int | None, section_id: int | None) -> bool:
    return _has_assignment(db, user.id, class_id, section_id, class_teacher_only=True)


def ensure_can_view_section(db: Db, user: CurrentUser, class_id: int | None, section_id: int | None) -> None:
    """Managers, or teachers assigned to the section."""
    if user.user_type in MANAGERS:
        return
    if user.user_type == "teacher" and is_assigned(db, user, class_id, section_id):
        return
    raise Forbidden("Not assigned to this class/section")


def ensure_can_edit_students(db: Db, user: CurrentUser, class_id: int | None, section_id: int | None) -> None:
    """Managers, or the CLASS TEACHER of the section."""
    if user.user_type in MANAGERS:
        return
    if user.user_type == "teacher" and is_class_teacher(db, user, class_id, section_id):
        return
    raise Forbidden("Only the class teacher (or admins) can manage students here")


def ensure_students_in_section(db: Db, section_id: int, student_ids: Iterable[int]) -> None:
    """Reject requests that touch students outside the section the caller was
    authorised for (e.g. marks or attendance for another class)."""
    wanted = set(student_ids)
    if not wanted:
        return
    placeholders = ",".join(["%s"] * len(wanted))
    rows = db.all(
        f"SELECT user_id FROM student_profiles WHERE org_id = %s AND section_id = %s AND user_id IN ({placeholders})",
        (db.org, section_id, *wanted),
    )
    outside = sorted(wanted - {r["user_id"] for r in rows})
    if outside:
        raise BadRequest(f"Students not in this section: {', '.join(map(str, outside))}")
