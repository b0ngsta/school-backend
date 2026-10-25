"""Messages from the school to parents (the student account): a new
timetable, a PTM date, a subject update, anything.

Senders: managers (any student, section, class or the whole school) and
class teachers (students of the sections they are class teacher of, or such
a section). Each recipient has a `message_recipients` row with its own read
state; the student app reads them from /student/messages.
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import BadRequest, Forbidden, NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.messages import MessageCreate
from app.services import attachments, audit, notify, pagination, storage

_STAFF_SELECT = (
    "SELECT m.id, m.title, m.body, m.audience, m.audience_label, m.class_id, m.section_id, m.recipients_count, "
    "m.attachment_path, m.attachment_name, m.attachment_type, m.sender_id, u.full_name AS sender_name, "
    "m.created_at, (SELECT COUNT(*) FROM message_recipients r WHERE r.message_id = m.id AND r.read_at IS NOT NULL) "
    "AS read_count FROM messages m LEFT JOIN users u ON u.id = m.sender_id WHERE m.org_id = %s"
)
_STUDENT_SELECT = (
    "SELECT m.id, m.title, m.body, m.audience, m.audience_label, m.attachment_path, m.attachment_name, "
    "m.attachment_type, m.created_at, r.read_at, u.full_name AS sender_name, u.photo_path, u.user_type AS sender_role "
    "FROM message_recipients r JOIN messages m ON m.id = r.message_id LEFT JOIN users u ON u.id = m.sender_id "
    "WHERE r.student_id = %s AND m.org_id = %s"
)
ORDER = "m.created_at DESC, m.id DESC"

_ACTIVE_STUDENTS = ("SELECT u.id, u.full_name, sp.class_id, sp.section_id FROM users u "
                    "JOIN student_profiles sp ON sp.user_id = u.id "
                    "WHERE u.org_id = %s AND u.user_type = 'student' AND u.is_active = 1")


def _class_teacher_sections(db: Db, user: CurrentUser) -> set[int]:
    return {r["section_id"] for r in db.all(
        "SELECT section_id FROM teacher_assignments WHERE org_id = %s AND teacher_id = %s AND role = 'class_teacher'",
        (db.org, user.id))}


def _resolve(db: Db, user: CurrentUser, body: MessageCreate) -> tuple[str, str, int | None, int | None, list[int]]:
    """(audience, label, class_id, section_id, recipient ids) — with the permission checks."""
    manager = user.user_type in MANAGERS
    if not manager and user.user_type != "teacher":
        raise Forbidden("Not allowed")
    own_sections = set() if manager else _class_teacher_sections(db, user)
    if not manager and not own_sections:
        raise Forbidden("Only class teachers (or admins) can message parents")

    if body.student_ids is not None:
        wanted = sorted(set(body.student_ids))
        placeholders = ",".join(["%s"] * len(wanted))
        rows = db.all(_ACTIVE_STUDENTS + f" AND u.id IN ({placeholders})", (db.org, *wanted))
        found = {r["id"]: r for r in rows}
        missing = [i for i in wanted if i not in found]
        if missing:
            raise NotFound(f"Students not found: {', '.join(map(str, missing))}")
        if not manager and any(r["section_id"] not in own_sections for r in rows):
            raise Forbidden("You can only message parents of your own class")
        label = rows[0]["full_name"] if len(rows) == 1 else f"{len(rows)} students"
        return "students", label[:150], None, None, wanted

    if body.section_id is not None:
        sec = db.one("SELECT s.id, s.class_id, s.name, c.name AS class_name FROM sections s "
                     "JOIN classes c ON c.id = s.class_id WHERE s.id = %s AND s.org_id = %s",
                     (body.section_id, db.org))
        if not sec:
            raise NotFound("Section not found")
        if not manager and sec["id"] not in own_sections:
            raise Forbidden("You can only message parents of your own class")
        ids = [r["id"] for r in db.all(_ACTIVE_STUDENTS + " AND sp.section_id = %s", (db.org, sec["id"]))]
        return "section", f"{sec['class_name']} — {sec['name']}"[:150], sec["class_id"], sec["id"], ids

    if not manager:
        raise Forbidden("Only admins can message a whole class or the whole school")
    if body.class_id is not None:
        cls = db.one("SELECT id, name FROM classes WHERE id = %s AND org_id = %s", (body.class_id, db.org))
        if not cls:
            raise NotFound("Class not found")
        ids = [r["id"] for r in db.all(_ACTIVE_STUDENTS + " AND sp.class_id = %s", (db.org, cls["id"]))]
        return "class", cls["name"][:150], cls["id"], None, ids
    ids = [r["id"] for r in db.all(_ACTIVE_STUDENTS, (db.org,))]
    return "school", "All students", None, None, ids


def send(db: Db, user: CurrentUser, body: MessageCreate) -> dict:
    with db.transaction():
        audience, label, class_id, section_id, ids = _resolve(db, user, body)
        if not ids:
            raise BadRequest("No students to send this message to")
        message_id = db.execute(
            "INSERT INTO messages (org_id, sender_id, title, body, audience, audience_label, class_id, section_id, "
            "recipients_count) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, user.id, body.title.strip(), body.body.strip(), audience, label, class_id, section_id, len(ids)),
        ).lastrowid
        db.execute_many("INSERT INTO message_recipients (message_id, student_id) VALUES (%s, %s)",
                        [(message_id, i) for i in ids])
        notify.notify(db, ids, "message", f"✉️ {body.title.strip()}", body.body.strip()[:120],
                      data={**notify.route("message", student=True), "params": {"id": message_id}})
        audit.record(db, user, "message.send", "message", message_id, {"audience": audience, "recipients": len(ids)})
    return get(db, user, message_id)


def _get(db: Db, message_id: int, lock: bool = False) -> dict:
    if lock:  # lock only the message row (not the joined user)
        db.one("SELECT id FROM messages WHERE id = %s AND org_id = %s FOR UPDATE", (message_id, db.org))
    row = db.one(_STAFF_SELECT + " AND m.id = %s", (db.org, message_id))
    if not row:
        raise NotFound("Message not found")
    return row


def _own(db: Db, user: CurrentUser, message_id: int, lock: bool = False) -> dict:
    msg = _get(db, message_id, lock)
    if user.user_type not in MANAGERS and msg["sender_id"] != user.id:
        raise NotFound("Message not found")
    return msg


def get(db: Db, user: CurrentUser, message_id: int) -> dict:
    return _own(db, user, message_id)


def list_sent(db: Db, user: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    """Managers: every message of the school. Teachers: the ones they sent."""
    if user.user_type in MANAGERS:
        return pagination.fetch(db, _STAFF_SELECT, (db.org,), ORDER, page)
    return pagination.fetch(db, _STAFF_SELECT + " AND m.sender_id = %s", (db.org, user.id), ORDER, page)


def recipients(db: Db, user: CurrentUser, message_id: int) -> list[dict]:
    _own(db, user, message_id)
    return db.all(
        "SELECT u.id AS student_id, u.full_name, sp.roll_no, c.name AS class_name, s.name AS section_name, r.read_at "
        "FROM message_recipients r JOIN users u ON u.id = r.student_id "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id LEFT JOIN classes c ON c.id = sp.class_id "
        "LEFT JOIN sections s ON s.id = sp.section_id WHERE r.message_id = %s "
        "ORDER BY c.name, s.name, CAST(sp.roll_no AS UNSIGNED), u.full_name", (message_id,))


def attach(db: Db, user: CurrentUser, message_id: int, file: UploadFile) -> dict:
    with db.transaction():
        msg = _own(db, user, message_id, lock=True)
        att = attachments.save(db, file, "messages", default_name="message")
        db.execute("UPDATE messages SET attachment_path = %s, attachment_name = %s, attachment_type = %s "
                   "WHERE id = %s", (*att.columns(), message_id))
        storage.delete_after_commit(db, msg["attachment_path"])
    return get(db, user, message_id)


def delete(db: Db, user: CurrentUser, message_id: int) -> None:
    with db.transaction():
        msg = _own(db, user, message_id, lock=True)
        db.execute("DELETE FROM messages WHERE id = %s", (message_id,))
        storage.delete_after_commit(db, msg["attachment_path"])
        audit.record(db, user, "message.delete", "message", message_id)


# --------------------------------------------------------------- students

def for_student(db: Db, student_id: int, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(db, _STUDENT_SELECT, (student_id, db.org), ORDER, page)


def open_for_student(db: Db, student_id: int, message_id: int) -> dict:
    """The message (marked read)."""
    row = db.one(_STUDENT_SELECT + " AND m.id = %s", (student_id, db.org, message_id))
    if not row:
        raise NotFound("Message not found")
    if row["read_at"] is None:
        db.execute("UPDATE message_recipients SET read_at = NOW() WHERE message_id = %s AND student_id = %s",
                   (message_id, student_id))
        row["read_at"] = db.scalar("SELECT read_at FROM message_recipients WHERE message_id = %s AND student_id = %s",
                                   (message_id, student_id))
    return row


def unread_count(db: Db, student_id: int) -> dict:
    count = db.scalar("SELECT COUNT(*) FROM message_recipients r JOIN messages m ON m.id = r.message_id "
                      "WHERE r.student_id = %s AND m.org_id = %s AND r.read_at IS NULL", (student_id, db.org))
    return {"count": int(count or 0)}
