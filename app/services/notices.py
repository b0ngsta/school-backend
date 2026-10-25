"""Notices — posted by managers, visible to all staff and to parents.
Optional attachment (PDF or photo, see services/attachments.py)."""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.notices import NoticeCreate
from app.services import attachments, notify, pagination, storage


def create_notice(db: Db, actor: CurrentUser, body: NoticeCreate) -> dict:
    with db.transaction():
        notice_id = db.execute(
            "INSERT INTO notices (org_id, created_by, title, body) VALUES (%s, %s, %s, %s)",
            (db.org, actor.id, body.title, body.body),
        ).lastrowid
        _notify_everyone(db, actor, body)
    return {"id": notice_id}


def _notify_everyone(db: Db, actor: CurrentUser, body: NoticeCreate) -> None:
    """Every active student (parent app) and staff member except the poster."""
    title, text = f"📢 {body.title}", (body.body or "")[:120] or None
    users = db.all("SELECT id, user_type FROM users WHERE org_id = %s AND is_active = 1 AND id != %s",
                   (db.org, actor.id))
    students = [u["id"] for u in users if u["user_type"] == "student"]
    staff = [u["id"] for u in users if u["user_type"] != "student"]
    notify.notify(db, students, "notice", title, text, data=notify.route("notice", student=True))
    notify.notify(db, staff, "notice", title, text, data=notify.route("notice", student=False))


def list_notices(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(
        db,
        "SELECT n.*, u.full_name AS posted_by FROM notices n JOIN users u ON u.id = n.created_by "
        "WHERE n.org_id = %s",
        (db.org,),
        "n.created_at DESC, n.id DESC",
        page,
    )


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
