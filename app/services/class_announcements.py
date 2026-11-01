"""Class announcements (migration 0018): a teacher posts a heading, a message
and optionally one PDF / photo to the sections they teach (class teacher or
subject teacher, from teacher_assignments). Managers may post to any section.

Students of those sections read them in the app (/student/class-announcements)
and get a push. The author or a manager may edit the text, change the file or
delete the announcement.
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import BadRequest, Forbidden, NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.class_announcements import ClassAnnouncementCreate, ClassAnnouncementUpdate
from app.services import attachments, audit, notify, pagination, storage

ORDER = "a.created_at DESC, a.id DESC"
_COLUMNS = ("a.id, a.title, a.body, a.audience_label, a.attachment_path, a.attachment_name, a.attachment_type, "
            "a.created_at, a.updated_at, u.full_name AS posted_by, u.photo_path AS posted_by_photo, "
            "u.user_type AS posted_by_role")
_STAFF_SELECT = (
    f"SELECT {_COLUMNS}, a.created_by, a.recipients_count, "
    "(SELECT GROUP_CONCAT(cs.section_id ORDER BY cs.section_id) FROM class_announcement_sections cs "
    " WHERE cs.announcement_id = a.id) AS section_ids "
    "FROM class_announcements a LEFT JOIN users u ON u.id = a.created_by WHERE a.org_id = %s"
)
_STUDENT_SELECT = (
    f"SELECT {_COLUMNS} FROM class_announcements a LEFT JOIN users u ON u.id = a.created_by "
    "WHERE a.org_id = %s AND EXISTS (SELECT 1 FROM class_announcement_sections cs "
    "WHERE cs.announcement_id = a.id AND cs.section_id = %s)"
)


def _is_manager(user: CurrentUser) -> bool:
    return user.user_type in MANAGERS


def _ids(rows: list[dict]) -> list[dict]:
    for row in rows:
        raw = row.get("section_ids")
        row["section_ids"] = [int(x) for x in raw.split(",")] if raw else []
    return rows


# ------------------------------------------------------------------ targets

def targets(db: Db, user: CurrentUser) -> list[dict]:
    """The sections the caller may post to, in class order. Teachers: the ones
    they are assigned to (with their subjects); managers: every section."""
    if _is_manager(user):
        rows = db.all(
            "SELECT c.id AS class_id, c.name AS class_name, s.id AS section_id, s.name AS section_name, "
            "NULL AS roles, NULL AS subjects FROM sections s JOIN classes c ON c.id = s.class_id "
            "WHERE s.org_id = %s ORDER BY c.name, s.name", (db.org,))
    else:
        rows = db.all(
            "SELECT c.id AS class_id, c.name AS class_name, s.id AS section_id, s.name AS section_name, "
            "GROUP_CONCAT(DISTINCT ta.role) AS roles, "
            "GROUP_CONCAT(DISTINCT ta.subject ORDER BY ta.subject SEPARATOR ', ') AS subjects "
            "FROM teacher_assignments ta JOIN sections s ON s.id = ta.section_id JOIN classes c ON c.id = s.class_id "
            "WHERE ta.org_id = %s AND ta.teacher_id = %s "
            "GROUP BY c.id, c.name, s.id, s.name ORDER BY c.name, s.name", (db.org, user.id))
    for r in rows:
        r["class_teacher"] = "class_teacher" in (r.pop("roles") or "")
        r["label"] = f"{r['class_name']} — {r['section_name']}"
    return rows


def _sections(db: Db, user: CurrentUser, section_ids: list[int]) -> list[dict]:
    wanted = sorted(set(section_ids))
    placeholders = ",".join(["%s"] * len(wanted))
    rows = db.all(
        "SELECT s.id, s.class_id, s.name, c.name AS class_name FROM sections s JOIN classes c ON c.id = s.class_id "
        f"WHERE s.org_id = %s AND s.id IN ({placeholders}) ORDER BY c.name, s.name", (db.org, *wanted))
    found = {r["id"] for r in rows}
    missing = [i for i in wanted if i not in found]
    if missing:
        raise NotFound(f"Sections not found: {', '.join(map(str, missing))}")
    if not _is_manager(user):
        mine = {r["section_id"] for r in db.all(
            "SELECT DISTINCT section_id FROM teacher_assignments WHERE org_id = %s AND teacher_id = %s",
            (db.org, user.id))}
        if any(r["id"] not in mine for r in rows):
            raise Forbidden("You can only post announcements to classes you teach")
    return rows


def _label(sections: list[dict]) -> str:
    """"Class 1 — A, B · Class 2 — A" (sections grouped under their class)."""
    groups: dict[str, list[str]] = {}
    for s in sections:
        groups.setdefault(s["class_name"], []).append(s["name"])
    return " · ".join(f"{c} — {', '.join(names)}" for c, names in groups.items())[:255]


# ------------------------------------------------------------------- write

def create(db: Db, user: CurrentUser, body: ClassAnnouncementCreate) -> dict:
    title, text = body.title.strip(), body.body.strip()
    if not title or not text:
        raise BadRequest("Heading and message are required")
    with db.transaction():
        sections = _sections(db, user, body.section_ids)
        section_ids = [s["id"] for s in sections]
        placeholders = ",".join(["%s"] * len(section_ids))
        students = [r["id"] for r in db.all(
            "SELECT u.id FROM users u JOIN student_profiles sp ON sp.user_id = u.id "
            "WHERE u.org_id = %s AND u.is_active = 1 AND u.user_type = 'student' "
            f"AND sp.section_id IN ({placeholders})",
            (db.org, *section_ids))]
        announcement_id = db.execute(
            "INSERT INTO class_announcements (org_id, created_by, title, body, audience_label, recipients_count) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (db.org, user.id, title, text, _label(sections), len(students)),
        ).lastrowid
        db.execute_many(
            "INSERT INTO class_announcement_sections (announcement_id, section_id, class_id) VALUES (%s, %s, %s)",
            [(announcement_id, s["id"], s["class_id"]) for s in sections])
        notify.notify(db, students, "class_announcement", f"📣 {title}", text[:120],
                      data={**notify.route("class_announcement", student=True), "params": {"id": announcement_id}})
        audit.record(db, user, "class_announcement.create", "class_announcement", announcement_id,
                     {"sections": section_ids, "recipients": len(students)})
    return get(db, user, announcement_id)


def _get(db: Db, announcement_id: int) -> dict:
    row = db.one(_STAFF_SELECT + " AND a.id = %s", (db.org, announcement_id))
    if not row:
        raise NotFound("Announcement not found")
    return _ids([row])[0]


def _own(db: Db, user: CurrentUser, announcement_id: int, lock: bool = False) -> dict:
    if lock:
        db.one("SELECT id FROM class_announcements WHERE id = %s AND org_id = %s FOR UPDATE",
               (announcement_id, db.org))
    row = _get(db, announcement_id)
    if not _is_manager(user) and row["created_by"] != user.id:
        raise NotFound("Announcement not found")
    return row


def get(db: Db, user: CurrentUser, announcement_id: int) -> dict:
    return _own(db, user, announcement_id)


def update(db: Db, user: CurrentUser, announcement_id: int, body: ClassAnnouncementUpdate) -> dict:
    title, text = body.title.strip(), body.body.strip()
    if not title or not text:
        raise BadRequest("Heading and message are required")
    with db.transaction():
        _own(db, user, announcement_id, lock=True)
        db.execute("UPDATE class_announcements SET title = %s, body = %s WHERE id = %s",
                   (title, text, announcement_id))
        audit.record(db, user, "class_announcement.update", "class_announcement", announcement_id)
    return get(db, user, announcement_id)


def delete(db: Db, user: CurrentUser, announcement_id: int) -> None:
    with db.transaction():
        row = _own(db, user, announcement_id, lock=True)
        db.execute("DELETE FROM class_announcements WHERE id = %s", (announcement_id,))
        storage.delete_after_commit(db, row["attachment_path"])
        audit.record(db, user, "class_announcement.delete", "class_announcement", announcement_id,
                     {"title": row["title"]})


def attach(db: Db, user: CurrentUser, announcement_id: int, file: UploadFile) -> dict:
    with db.transaction():
        row = _own(db, user, announcement_id, lock=True)
        att = attachments.save(db, file, "class_announcements", default_name="announcement")
        db.execute("UPDATE class_announcements SET attachment_path = %s, attachment_name = %s, attachment_type = %s "
                   "WHERE id = %s", (*att.columns(), announcement_id))
        storage.delete_after_commit(db, row["attachment_path"])
    return get(db, user, announcement_id)


def remove_attachment(db: Db, user: CurrentUser, announcement_id: int) -> None:
    with db.transaction():
        row = _own(db, user, announcement_id, lock=True)
        db.execute("UPDATE class_announcements SET attachment_path = NULL, attachment_name = NULL, "
                   "attachment_type = NULL WHERE id = %s", (announcement_id,))
        storage.delete_after_commit(db, row["attachment_path"])


# -------------------------------------------------------------------- read

def list_staff(db: Db, user: CurrentUser, page: pagination.Page | None = None,
               section_id: int | None = None) -> pagination.PageResult:
    """Managers: every announcement of the school. Teachers: their own."""
    sql, params = _STAFF_SELECT, [db.org]
    if not _is_manager(user):
        sql += " AND a.created_by = %s"
        params.append(user.id)
    if section_id is not None:
        sql += " AND EXISTS (SELECT 1 FROM class_announcement_sections cs WHERE cs.announcement_id = a.id " \
               "AND cs.section_id = %s)"
        params.append(section_id)
    result = pagination.fetch(db, sql, params, ORDER, page)
    _ids(result.rows)
    return result


def _student_section(db: Db, uid: int) -> int | None:
    me = db.one("SELECT section_id FROM student_profiles WHERE user_id = %s AND org_id = %s", (uid, db.org))
    return me["section_id"] if me else None


def list_for_student(db: Db, uid: int, page: pagination.Page | None = None) -> pagination.PageResult:
    section = _student_section(db, uid)
    if section is None:
        return pagination.PageResult([], 0 if page and page.requested else None)
    return pagination.fetch(db, _STUDENT_SELECT, (db.org, section), ORDER, page)


def get_for_student(db: Db, uid: int, announcement_id: int) -> dict:
    section = _student_section(db, uid)
    row = db.one(_STUDENT_SELECT + " AND a.id = %s", (db.org, section, announcement_id)) if section else None
    if not row:
        raise NotFound("Announcement not found")
    return row
