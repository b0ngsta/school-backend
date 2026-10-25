"""Homework for a whole class-section, with a per-student submission status.

view / assign / mark submissions: managers, or teachers assigned to the
section (class or subject teacher). Edit / delete: the teacher who set it,
or managers. A student without a homework_submissions row is 'pending', so
students who join the section later see the homework automatically.
"""
from __future__ import annotations

from datetime import date

from fastapi import UploadFile

from app.core.clock import school_today
from app.core.errors import BadRequest, Forbidden, NotFound, Unprocessable
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.homework import ClassHomeworkCreate, ClassHomeworkUpdate, SubmissionsUpdate
from app.services import access, attachments, notify, pagination, storage

# counts are over the section's current (active) students
HOMEWORK_SELECT = (
    "SELECT h.id, h.class_id, h.section_id, c.name AS class_name, s.name AS section_name, h.subject, "
    "h.title, h.note, h.due_date, h.created_by, u.full_name AS assigned_by, h.created_at, h.updated_at, "
    "h.attachment_path, h.attachment_name, h.attachment_type, "
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
    row = db.one("SELECT class_id FROM sections WHERE id = %s AND org_id = %s", (section_id, db.org))
    if not row:
        raise NotFound("Section not found")
    return row["class_id"]


def _get(db: Db, homework_id: int) -> dict:
    row = db.one(HOMEWORK_SELECT + " WHERE h.id = %s AND h.org_id = %s", (homework_id, db.org))
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
    sql, where, params = HOMEWORK_SELECT, ["h.org_id = %s"], [db.org]
    if class_id is not None:
        where.append("h.class_id = %s")
        params.append(class_id)
    if section_id is not None:
        where.append("h.section_id = %s")
        params.append(section_id)
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
            "INSERT INTO class_homework (org_id, class_id, section_id, subject, title, note, due_date, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, body.class_id, body.section_id, (body.subject or "").strip() or None, body.title.strip(),
             body.note, body.due_date, user.id),
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
    with db.transaction():
        hw = _editable(db, user, homework_id)
        db.execute("DELETE FROM class_homework WHERE id = %s", (homework_id,))
        storage.delete_after_commit(db, hw["attachment_path"])


def attach(db: Db, user: CurrentUser, homework_id: int, file: UploadFile) -> dict:
    """Add or replace the homework sheet (PDF or photo) — same rights as editing."""
    with db.transaction():
        db.one("SELECT id FROM class_homework WHERE id = %s AND org_id = %s FOR UPDATE", (homework_id, db.org))
        hw = _editable(db, user, homework_id)
        att = attachments.save(db, file, "homework", default_name="homework")
        db.execute("UPDATE class_homework SET attachment_path = %s, attachment_name = %s, attachment_type = %s "
                   "WHERE id = %s", (*att.columns(), homework_id))
        storage.delete_after_commit(db, hw["attachment_path"])
    return _get(db, homework_id)


def remove_attachment(db: Db, user: CurrentUser, homework_id: int) -> None:
    with db.transaction():
        hw = _editable(db, user, homework_id)
        db.execute("UPDATE class_homework SET attachment_path = NULL, attachment_name = NULL, "
                   "attachment_type = NULL WHERE id = %s", (homework_id,))
        storage.delete_after_commit(db, hw["attachment_path"])


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
        "'class' AS scope, h.id AS homework_id, h.attachment_path, h.attachment_name, h.attachment_type "
        "FROM class_homework h "
        "JOIN student_profiles sp ON sp.section_id = h.section_id AND sp.class_id = h.class_id AND sp.user_id = %s "
        "JOIN users u ON u.id = h.created_by "
        "LEFT JOIN homework_submissions hs ON hs.homework_id = h.id AND hs.student_id = sp.user_id "
        "UNION ALL "
        "SELECT hw.id, hw.student_id, hw.title, hw.due_date, hw.status, hw.note, hw.created_by, hw.created_at, "
        "hw.updated_at, u.full_name AS created_by_name, NULL AS subject, 'student' AS scope, NULL AS homework_id, "
        "NULL AS attachment_path, NULL AS attachment_name, NULL AS attachment_type "
        "FROM homeworks hw JOIN users u ON u.id = hw.created_by WHERE hw.student_id = %s"
        ") AS hw"
    )


HOMEWORK_ORDER = "hw.created_at DESC, hw.scope, hw.id DESC"
GENERAL_SUBJECT = "General"  # the subject shown for homework that names none


def student_homework_filters(given_on: date | None, subject: str | None) -> tuple[str, list]:
    """WHERE clause (on the merged rows of `student_homework_sql`) for the
    student app's filters: the day the homework was given, and the subject
    (case-insensitive; "General" = homework without a subject)."""
    where, params = [], []
    if given_on is not None:
        where.append("hw.created_at >= %s AND hw.created_at < %s + INTERVAL 1 DAY")
        params += [given_on, given_on]
    if subject is not None:
        subject = subject.strip()
        if subject.lower() == GENERAL_SUBJECT.lower():
            where.append("(hw.subject IS NULL OR TRIM(hw.subject) = '')")
        else:
            where.append("LOWER(TRIM(hw.subject)) = LOWER(%s)")
            params.append(subject)
    return (" WHERE " + " AND ".join(where)) if where else "", params



def subjects_for_student(db: Db, student_id: int) -> list[dict]:
    """The subjects of the student's class (class order), each with how much
    class homework it has and the day the latest was given; subjects named
    only on homework follow; homework without a subject is "General"."""
    me = db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s AND org_id = %s",
                (student_id, db.org)) or {}
    subjects = [r["name"] for r in db.all(
        "SELECT cs.name FROM class_subjects cs JOIN classes c ON c.id = cs.class_id "
        "WHERE cs.class_id = %s AND c.org_id = %s ORDER BY cs.id", (me.get("class_id"), db.org))]
    stats = db.all(
        "SELECT COALESCE(NULLIF(TRIM(h.subject), ''), %s) AS subject, COUNT(*) AS count, "
        "MAX(h.created_at) AS last_given FROM class_homework h "
        "WHERE h.org_id = %s AND h.section_id <=> %s AND h.class_id <=> %s "
        "GROUP BY COALESCE(NULLIF(TRIM(h.subject), ''), %s)",
        (GENERAL_SUBJECT, db.org, me.get("section_id"), me.get("class_id"), GENERAL_SUBJECT),
    )
    out: dict[str, dict] = {}
    for name in subjects:
        out.setdefault(name.strip().lower(), {"subject": name.strip(), "count": 0, "last_given": None})
    for row in stats:
        entry = out.setdefault(row["subject"].lower(), {"subject": row["subject"], "count": 0, "last_given": None})
        entry["count"] += int(row["count"])
        if row["last_given"] and (entry["last_given"] is None or row["last_given"] > entry["last_given"]):
            entry["last_given"] = row["last_given"]
    general = out.pop(GENERAL_SUBJECT.lower(), None)
    return list(out.values()) + ([general] if general else [])


def class_homework_for_student(db: Db, student_id: int, homework_id: int) -> dict | None:
    """The class homework if it belongs to the student's current section."""
    return db.one(
        "SELECT h.id FROM class_homework h JOIN student_profiles sp ON sp.section_id = h.section_id "
        "AND sp.class_id = h.class_id AND sp.user_id = %s WHERE h.id = %s",
        (student_id, homework_id),
    )
