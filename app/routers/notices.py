"""Notices — posted by admin/principal, visible to all staff."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import STAFF
from app.schemas import NoticeCreate

router = APIRouter(prefix="/notices", tags=["notices"])

poster = require_roles("admin", "principal")
staff = require_roles(*STAFF, "driver")  # drivers can read notices too


@router.post("", status_code=201)
def create_notice(body: NoticeCreate, actor: dict = Depends(poster)):
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO notices (created_by, title, body) VALUES (%s, %s, %s)",
            (actor["id"], body.title, body.body),
        )
        return {"id": cur.lastrowid}


@router.get("")
def list_notices(user: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT n.*, u.full_name AS posted_by FROM notices n "
            "JOIN users u ON u.id = n.created_by ORDER BY n.created_at DESC"
        )
        return cur.fetchall()


@router.delete("/{notice_id}")
def delete_notice(notice_id: int, actor: dict = Depends(poster)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Notice not found")
    return {"deleted": notice_id}
