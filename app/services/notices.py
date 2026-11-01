"""Notices — posted by managers, for the whole school or for chosen classes.
Optional attachment (PDF or photo, see services/attachments.py).

Who sees a notice (migration 0018):
- audience 'school'  → everyone (students, all staff) — every notice before 0018
- audience 'classes' → students of the classes in `notice_classes`, managers,
  and teachers assigned to one of those classes. Only they get its push.
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.notices import NoticeCreate
from app.services import attachments, audit, notify, pagination, storage

_CLASS_IDS = "(SELECT GROUP_CONCAT(nc.class_id ORDER BY nc.class_id) FROM notice_classes nc " \
             "WHERE nc.notice_id = n.id) AS class_ids"


# ------------------------------------------------------------------ who sees it

def student_scope(db: Db, uid: int) -> tuple[str, tuple]:
    """SQL condition (alias `n`) + params: the notices a student may see."""
    me = db.one("SELECT class_id FROM student_profiles WHERE user_id = %s AND org_id = %s", (uid, db.org))
    class_id = me["class_id"] if me else None
    if class_id is None:
        return "n.audience = 'school'", ()
    return ("(n.audience = 'school' OR EXISTS (SELECT 1 FROM notice_classes nc "
            "WHERE nc.notice_id = n.id AND nc.class_id = %s))", (class_id,))


def staff_scope(user: CurrentUser) -> tuple[str, tuple]:
    """SQL condition (alias `n`) + params: the notices a staff member sees.
    Managers: all. Teachers: school-wide + classes they teach. Drivers: school-wide."""
    if user.user_type in MANAGERS:
        return "1 = 1", ()
    if user.user_type == "teacher":
        return ("(n.audience = 'school' OR n.created_by = %s OR EXISTS (SELECT 1 FROM notice_classes nc "
                "JOIN teacher_assignments ta ON ta.class_id = nc.class_id "
                "WHERE nc.notice_id = n.id AND ta.teacher_id = %s))", (user.id, user.id))
    return "(n.audience = 'school' OR n.created_by = %s)", (user.id,)


def with_class_ids(rows: list[dict]) -> list[dict]:
    for row in rows:
        raw = row.get("class_ids")
        row["class_ids"] = [int(x) for x in raw.split(",")] if raw else []
    return rows


# ---------------------------------------------------------------------- write

def _classes(db: Db, class_ids: list[int]) -> list[dict]:
    wanted = sorted(set(class_ids))
    placeholders = ",".join(["%s"] * len(wanted))
    rows = db.all(f"SELECT id, name FROM classes WHERE org_id = %s AND id IN ({placeholders}) ORDER BY name",
                  (db.org, *wanted))
    found = {r["id"] for r in rows}
    missing = [i for i in wanted if i not in found]
    if missing:
        raise NotFound(f"Classes not found: {', '.join(map(str, missing))}")
    return rows


def create_notice(db: Db, actor: CurrentUser, body: NoticeCreate) -> dict:
    with db.transaction():
        classes = _classes(db, body.class_ids) if body.class_ids else []
        audience = "classes" if classes else "school"
        label = ", ".join(c["name"] for c in classes)[:255] if classes else "Whole school"
        notice_id = db.execute(
            "INSERT INTO notices (org_id, created_by, title, body, audience, audience_label) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (db.org, actor.id, body.title, body.body, audience, label),
        ).lastrowid
        if classes:
            db.execute_many("INSERT INTO notice_classes (notice_id, class_id) VALUES (%s, %s)",
                            [(notice_id, c["id"]) for c in classes])
        _notify(db, actor, body, [c["id"] for c in classes])
        if classes:
            audit.record(db, actor, "notice.create", "notice", notice_id,
                         {"audience": audience, "classes": [c["id"] for c in classes]})
    return {"id": notice_id, "audience": audience, "audience_label": label,
            "class_ids": [c["id"] for c in classes]}


def _notify(db: Db, actor: CurrentUser, body: NoticeCreate, class_ids: list[int]) -> None:
    """Whole school: every active student (parent app) and staff member except
    the poster. Some classes: their students, managers, their teachers."""
    title, text = f"📢 {body.title}", (body.body or "")[:120] or None
    if not class_ids:
        users = db.all("SELECT id, user_type FROM users WHERE org_id = %s AND is_active = 1 AND id != %s",
                       (db.org, actor.id))
        students = [u["id"] for u in users if u["user_type"] == "student"]
        staff = [u["id"] for u in users if u["user_type"] != "student"]
    else:
        placeholders = ",".join(["%s"] * len(class_ids))
        students = [r["id"] for r in db.all(
            "SELECT u.id FROM users u JOIN student_profiles sp ON sp.user_id = u.id "
            f"WHERE u.org_id = %s AND u.is_active = 1 AND u.user_type = 'student' AND sp.class_id IN ({placeholders})",
            (db.org, *class_ids))]
        teachers = [r["teacher_id"] for r in db.all(
            "SELECT DISTINCT ta.teacher_id FROM teacher_assignments ta JOIN users u ON u.id = ta.teacher_id "
            f"WHERE ta.org_id = %s AND u.is_active = 1 AND ta.class_id IN ({placeholders})",
            (db.org, *class_ids))]
        staff = [i for i in notify.manager_ids(db, exclude=actor.id) + teachers if i != actor.id]
    notify.notify(db, students, "notice", title, text, data=notify.route("notice", student=True))
    notify.notify(db, staff, "notice", title, text, data=notify.route("notice", student=False))


# ----------------------------------------------------------------------- read

def list_notices(db: Db, user: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    scope, params = staff_scope(user)
    result = pagination.fetch(
        db,
        f"SELECT n.*, u.full_name AS posted_by, {_CLASS_IDS} FROM notices n "
        f"LEFT JOIN users u ON u.id = n.created_by WHERE n.org_id = %s AND {scope}",
        (db.org, *params),
        "n.created_at DESC, n.id DESC",
        page,
    )
    with_class_ids(result.rows)
    return result


def delete_notice(db: Db, notice_id: int) -> dict:
    with db.transaction():
        notice = _lock(db, notice_id)
        db.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
        storage.delete_after_commit(db, notice["attachment_path"])
    return {"deleted": notice_id}


def _lock(db: Db, notice_id: int) -> dict:
    notice = db.one("SELECT id, attachment_path FROM notices WHERE id = %s AND org_id = %s FOR UPDATE",
                    (notice_id, db.org))
    if not notice:
        raise NotFound("Notice not found")
    return notice


def attach(db: Db, actor: CurrentUser, notice_id: int, file: UploadFile) -> dict:
    with db.transaction():
        notice = _lock(db, notice_id)
        att = attachments.save(db, file, "notices", default_name="notice")
        db.execute("UPDATE notices SET attachment_path = %s, attachment_name = %s, attachment_type = %s "
                   "WHERE id = %s", (*att.columns(), notice_id))
        storage.delete_after_commit(db, notice["attachment_path"])
    return {"id": notice_id, "attachment_path": att.path, "attachment_name": att.name,
            "attachment_type": att.type}


def remove_attachment(db: Db, actor: CurrentUser, notice_id: int) -> None:
    with db.transaction():
        notice = _lock(db, notice_id)
        db.execute("UPDATE notices SET attachment_path = NULL, attachment_name = NULL, attachment_type = NULL "
                   "WHERE id = %s", (notice_id,))
        storage.delete_after_commit(db, notice["attachment_path"])
