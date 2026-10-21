"""Notices — posted by admin/principal, visible to all staff."""
from __future__ import annotations

from app.core.errors import NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.notices import NoticeCreate
from app.services import notify, pagination


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
    if db.execute("DELETE FROM notices WHERE id = %s AND org_id = %s", (notice_id, db.org)).rowcount == 0:
        raise NotFound("Notice not found")
    return {"deleted": notice_id}
