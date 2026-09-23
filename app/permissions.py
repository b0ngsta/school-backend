"""Shared role/assignment permission helpers."""
from fastapi import HTTPException, status

MANAGERS = ("admin", "principal", "sub_admin", "coordinator")
STAFF = MANAGERS + ("teacher",)


def get_assignments(cur, teacher_id: int, class_id: int | None = None,
                    section_id: int | None = None) -> list[dict]:
    sql = (
        "SELECT ta.*, c.name AS class_name, s.name AS section_name, "
        "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.section_id = ta.section_id) AS student_count "
        "FROM teacher_assignments ta "
        "JOIN classes c ON c.id = ta.class_id "
        "JOIN sections s ON s.id = ta.section_id "
        "WHERE ta.teacher_id = %s"
    )
    params: list = [teacher_id]
    if class_id is not None:
        sql += " AND ta.class_id = %s"
        params.append(class_id)
    if section_id is not None:
        sql += " AND ta.section_id = %s"
        params.append(section_id)
    cur.execute(sql, tuple(params))
    return cur.fetchall()


def is_assigned(cur, user: dict, class_id: int, section_id: int | None) -> bool:
    """Teacher has ANY assignment (class or subject) on this class/section."""
    return bool(get_assignments(cur, user["id"], class_id, section_id))


def is_class_teacher(cur, user: dict, class_id: int, section_id: int | None) -> bool:
    rows = get_assignments(cur, user["id"], class_id, section_id)
    return any(r["role"] == "class_teacher" for r in rows)


def ensure_can_view_section(cur, user: dict, class_id: int, section_id: int | None):
    """Managers, or teachers assigned to the section."""
    if user["user_type"] in MANAGERS:
        return
    if user["user_type"] == "teacher" and is_assigned(cur, user, class_id, section_id):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Not assigned to this class/section")


def ensure_can_edit_students(cur, user: dict, class_id: int, section_id: int | None):
    """Managers, or the CLASS TEACHER of the section."""
    if user["user_type"] in MANAGERS:
        return
    if user["user_type"] == "teacher" and is_class_teacher(cur, user, class_id, section_id):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN, "Only the class teacher (or admins) can manage students here"
    )
