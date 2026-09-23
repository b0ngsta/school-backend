"""Notices — posted by admin, visible to all teachers (and staff)."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import require_roles
from app.schemas import NoticeCreate

router = APIRouter(prefix="/notices", tags=["notices"])


@router.post("", status_code=201)
def create_notice(body: NoticeCreate, admin: dict = Depends(require_roles("admin"))):
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO notices (created_by, title, body) VALUES (%s, %s, %s)",
            (admin["id"], body.title, body.body),
        )
        return {"id": cur.lastrowid}


@router.get("")
def list_notices(user: dict = Depends(require_roles("admin", "sub_admin", "teacher"))):
    with db_cursor() as cur:
        cur.execute(
            "SELECT n.*, u.full_name AS posted_by FROM notices n "
            "JOIN users u ON u.id = n.created_by ORDER BY n.created_at DESC"
        )
        return cur.fetchall()


@router.delete("/{notice_id}")
def delete_notice(notice_id: int, admin: dict = Depends(require_roles("admin"))):
    with db_cursor() as cur:
        cur.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Notice not found")
    return {"deleted": notice_id}
