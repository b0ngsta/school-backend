"""Notices — posted by admin/principal, visible to all staff."""
from __future__ import annotations

from app.core.errors import NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.notices import NoticeCreate
from app.services import pagination


def create_notice(db: Db, actor: CurrentUser, body: NoticeCreate) -> dict:
    notice_id = db.execute(
        "INSERT INTO notices (created_by, title, body) VALUES (%s, %s, %s)",
        (actor.id, body.title, body.body),
    ).lastrowid
    return {"id": notice_id}


def list_notices(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(
        db,
        "SELECT n.*, u.full_name AS posted_by FROM notices n JOIN users u ON u.id = n.created_by",
        (),
        "n.created_at DESC, n.id DESC",
        page,
    )


def delete_notice(db: Db, notice_id: int) -> dict:
    if db.execute("DELETE FROM notices WHERE id = %s", (notice_id,)).rowcount == 0:
        raise NotFound("Notice not found")
    return {"deleted": notice_id}
