"""Homework for a whole class-section, with a per-student submission status.

view / assign / mark submissions: managers, or teachers assigned to the
section (class or subject teacher). Edit / delete: the teacher who set it,
or managers. A student without a homework_submissions row is 'pending', so
students who join the section later see the homework automatically.
"""
from __future__ import annotations

from app.core.clock import school_today
from app.core.errors import BadRequest, Forbidden, NotFound, Unprocessable
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.homework import ClassHomeworkCreate, ClassHomeworkUpdate, SubmissionsUpdate
from app.services import access, notify, pagination

# counts are over the section's current (active) students
HOMEWORK_SELECT = (
    "SELECT h.id, h.class_id, h.section_id, c.name AS class_name, s.name AS section_name, h.subject, "
    "h.title, h.note, h.due_date, h.created_by, u.full_name AS assigned_by, h.created_at, h.updated_at, "
    "(SELECT COUNT(*) FROM student_profiles sp JOIN users su ON su.id = sp.user_id AND su.is_active = 1 "
    " WHERE sp.section_id = h.section_id) AS count_total, "
    "(SELECT COUNT(*) FROM homework_submissions hs JOIN student_profiles sp ON sp.user_id = hs.student_id "
    " AND sp.section_id = h.section_id JOIN users su ON su.id = hs.student_id AND su.is_active = 1 "
    " WHERE hs.homework_id = h.id AND hs.status = 'submitted') AS count_submitted, "
    "(SELECT COUNT(*) FROM homework_submissions hs JOIN student_profiles sp ON sp.user_id = hs.student_id "
    " AND sp.section_id = h.section_id JOIN users su ON su.id = hs.student_id AND su.is_active = 1 "
    " WHERE hs.homework_id = h.id AND hs.status = 'late') AS count_late "
    "FROM class_homework h "
    "JOIN classes c ON c.id = h.class_id "
    "JOIN sections s ON s.id = h.section_id "
    "JOIN users u ON u.id = h.created_by"
)


def _shape(row: dict) -> dict:
    total, submitted, late = (int(row.pop(k) or 0) for k in ("count_total", "count_submitted", "count_late"))
    row["counts"] = {"total": total, "submitted": submitted, "late": late,
                     "pending": max(total - submitted - late, 0)}
    return row


def _section_class(db: Db, section_id: int) -> int:
    row = db.one("SELECT class_id FROM sections WHERE id = %s", (section_id,))
    if not row:
        raise NotFound("Section not found")
    return row["class_id"]


def _get(db: Db, homework_id: int) -> dict:
    row = db.one(HOMEWORK_SELECT + " WHERE h.id = %s", (homework_id,))
    if not row:
        raise NotFound("Homework not found")
    return _shape(row)


def _viewable(db: Db, user: CurrentUser, homework_id: int) -> dict:
    hw = _get(db, homework_id)
    access.ensure_can_view_section(db, user, hw["class_id"], hw["section_id"])
    return hw


def _editable(db: Db, user: CurrentUser, homework_id: int) -> dict:
    hw = _get(db, homework_id)
    if user.user_type not in MANAGERS and hw["created_by"] != user.id:
        access.ensure_can_view_section(db, user, hw["class_id"], hw["section_id"])  # 403 if not even assigned
        raise Forbidden("Only the teacher who set this homework (or admins) can change it")
    return hw


def _check_due(due_date) -> None:
    if due_date is not None and due_date < school_today():
        raise Unprocessable("due_date can't be in the past")


def _students_of(db: Db, section_id: int) -> list[int]:
    return [r["user_id"] for r in db.all(
        "SELECT sp.user_id FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE sp.section_id = %s AND u.is_active = 1", (section_id,))]


def _label(hw) -> str:
    return " — ".join(p for p in (hw.subject, hw.title) if p)


# ------------------------------------------------------------------ CRUD

def list_homework(db: Db, user: CurrentUser, class_id: int | None, section_id: int | None,
                  page: pagination.Page | None = None) -> pagination.PageResult:
    if user.user_type not in MANAGERS and (class_id is None or section_id is None):
        raise BadRequest("class_id and section_id required")
    if section_id is not None and class_id is None:
        class_id = _section_class(db, section_id)
    if user.user_type not in MANAGERS:
        access.ensure_can_view_section(db, user, class_id, section_id)
    sql, where, params = HOMEWORK_SELECT, [], []
    if class_id is not None:
        where.append("h.class_id = %s")
        params.append(class_id)
    if section_id is not None:
        where.append("h.section_id = %s")
        params.append(section_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    result = pagination.fetch(db, sql, params, "h.created_at DESC, h.id DESC", page)
    return pagination.PageResult([_shape(r) for r in result.rows], result.total)


def create_homework(db: Db, user: CurrentUser, body: ClassHomeworkCreate) -> dict:
    with db.transaction():
        if _section_class(db, body.section_id) != body.class_id:
            raise NotFound("Section not in that class")
        access.ensure_can_view_section(db, user, body.class_id, body.section_id)
        _check_due(body.due_date)
        homework_id = db.execute(
            "INSERT INTO class_homework (class_id, section_id, subject, title, note, due_date, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (body.class_id, body.section_id, (body.subject or "").strip() or None, body.title.strip(), body.note,
             body.due_date, user.id),
        ).lastrowid
        title = f"📚 New homework: {_label(body)}"
        if body.due_date:
            title += f" (due {notify.day_month(body.due_date)})"
        notify.notify(db, _students_of(db, body.section_id), "homework", title, body.note or None,
                      data=notify.route("homework", student=True))
    return _get(db, homework_id)


def update_homework(db: Db, user: CurrentUser, homework_id: int, body: ClassHomeworkUpdate) -> dict:
    with db.transaction():
        hw = _editable(db, user, homework_id)
        if (body.class_id is not None and body.class_id != hw["class_id"]) or (
                body.section_id is not None and body.section_id != hw["section_id"]):
            raise BadRequest("The class and section of a homework can't be changed")
        due_changed = body.due_date != hw["due_date"]
        if due_changed:
            _check_due(body.due_date)
        db.execute(
            "UPDATE class_homework SET subject = %s, title = %s, note = %s, due_date = %s WHERE id = %s",
            ((body.subject or "").strip() or None, body.title.strip(), body.note, body.due_date, homework_id),
        )
        if due_changed:
            when = f"now due {notify.day_month(body.due_date)}" if body.due_date else "no due date any more"
            notify.notify(db, _students_of(db, hw["section_id"]), "homework",
                          f"📚 Homework due date changed: {_label(body)} ({when})",
                          data=notify.route("homework", student=True))
    return _get(db, homework_id)


def delete_homework(db: Db, user: CurrentUser, homework_id: int) -> None:
    _editable(db, user, homework_id)
    db.execute("DELETE FROM class_homework WHERE id = %s", (homework_id,))


# ------------------------------------------------------------ submissions

def submissions(db: Db, user: CurrentUser, homework_id: int) -> list[dict]:
    hw = _viewable(db, user, homework_id)
    return db.all(
        "SELECT u.id AS student_id, u.full_name, sp.roll_no, u.photo_path, "
        "COALESCE(hs.status, 'pending') AS status "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "LEFT JOIN homework_submissions hs ON hs.homework_id = %s AND hs.student_id = u.id "
        "WHERE sp.section_id = %s AND u.is_active = 1 "
        "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name, u.id",
        (homework_id, hw["section_id"]),
    )


def save_submissions(db: Db, user: CurrentUser, homework_id: int, body: SubmissionsUpdate) -> None:
    with db.transaction():
        hw = _viewable(db, user, homework_id)
        access.ensure_students_in_section(db, hw["section_id"], (i.student_id for i in body.items))
        set_status(db, user, homework_id, [(i.student_id, i.status) for i in body.items])


def set_status(db: Db, user: CurrentUser, homework_id: int, items: list[tuple[int, str]]) -> None:
    db.execute_many(
        "INSERT INTO homework_submissions (homework_id, student_id, status, updated_by) VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE status = VALUES(status), updated_by = VALUES(updated_by)",
        [(homework_id, student_id, status, user.id) for student_id, status in items],
    )


# ------------------------------------- a student's homework (class + legacy)

def student_homework_sql(columns: str) -> str:
    """Class homework of the student's current section merged with the old
    per-student rows. Params: (student_id, student_id, student_id).
    `columns` picks the output columns from the merged rows."""
    return (
        f"SELECT {columns} FROM ("
        "SELECT h.id, %s AS student_id, h.title, h.due_date, COALESCE(hs.status, 'pending') AS status, h.note, "
        "h.created_by, h.created_at, h.updated_at, u.full_name AS created_by_name, h.subject, "
        "'class' AS scope, h.id AS homework_id "
        "FROM class_homework h "
        "JOIN student_profiles sp ON sp.section_id = h.section_id AND sp.class_id = h.class_id AND sp.user_id = %s "
        "JOIN users u ON u.id = h.created_by "
        "LEFT JOIN homework_submissions hs ON hs.homework_id = h.id AND hs.student_id = sp.user_id "
        "UNION ALL "
        "SELECT hw.id, hw.student_id, hw.title, hw.due_date, hw.status, hw.note, hw.created_by, hw.created_at, "
        "hw.updated_at, u.full_name AS created_by_name, NULL AS subject, 'student' AS scope, NULL AS homework_id "
        "FROM homeworks hw JOIN users u ON u.id = hw.created_by WHERE hw.student_id = %s"
        ") AS hw"
    )


HOMEWORK_ORDER = "hw.created_at DESC, hw.scope, hw.id DESC"


def class_homework_for_student(db: Db, student_id: int, homework_id: int) -> dict | None:
    """The class homework if it belongs to the student's current section."""
    return db.one(
        "SELECT h.id FROM class_homework h JOIN student_profiles sp ON sp.section_id = h.section_id "
        "AND sp.class_id = h.class_id AND sp.user_id = %s WHERE h.id = %s",
        (student_id, homework_id),
    )
