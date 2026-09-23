"""Reception — admission enquiries (managers only)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS

router = APIRouter(prefix="/enquiries", tags=["reception"])

manager = require_roles(*MANAGERS)

EnquiryStatus = Literal["new", "follow_up", "converted", "closed"]


class EnquiryCreate(BaseModel):
    parent_name: str = Field(min_length=1, max_length=100)
    student_name: str = Field(min_length=1, max_length=100)
    class_interested: str | None = None
    contact: str = Field(min_length=5, max_length=20)
    email: str | None = None
    notes: str | None = None


class EnquiryUpdate(BaseModel):
    parent_name: str | None = None
    student_name: str | None = None
    class_interested: str | None = None
    contact: str | None = None
    email: str | None = None
    notes: str | None = None
    status: EnquiryStatus | None = None


@router.get("/stats")
def enquiry_stats(actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT "
            "SUM(DATE(created_at) = CURDATE()) AS today, "
            "SUM(status = 'converted') AS converted, "
            "SUM(status = 'new') AS pending, "
            "SUM(status = 'follow_up') AS follow_ups, "
            "COUNT(*) AS total "
            "FROM enquiries"
        )
        row = cur.fetchone()
    return {k: int(v or 0) for k, v in row.items()}


@router.get("")
def list_enquiries(status_filter: str | None = None, actor: dict = Depends(manager)):
    sql = "SELECT * FROM enquiries"
    params: tuple = ()
    if status_filter:
        sql += " WHERE status = %s"
        params = (status_filter,)
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY created_at DESC", params)
        return cur.fetchall()


@router.post("", status_code=201)
def create_enquiry(body: EnquiryCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO enquiries "
            "(parent_name, student_name, class_interested, contact, email, notes, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                body.parent_name, body.student_name, body.class_interested,
                body.contact, body.email, body.notes, actor["id"],
            ),
        )
        return {"id": cur.lastrowid}


@router.put("/{enquiry_id}")
def update_enquiry(enquiry_id: int, body: EnquiryUpdate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM enquiries WHERE id = %s", (enquiry_id,))
        e = cur.fetchone()
        if not e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Enquiry not found")
        data = body.model_dump(exclude_unset=True)
        merged = {**e, **{k: v for k, v in data.items()}}
        cur.execute(
            "UPDATE enquiries SET parent_name=%s, student_name=%s, class_interested=%s, "
            "contact=%s, email=%s, notes=%s, status=%s WHERE id=%s",
            (
                merged["parent_name"], merged["student_name"], merged["class_interested"],
                merged["contact"], merged["email"], merged["notes"], merged["status"],
                enquiry_id,
            ),
        )
    return {"updated": enquiry_id}


@router.delete("/{enquiry_id}")
def delete_enquiry(enquiry_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM enquiries WHERE id = %s", (enquiry_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Enquiry not found")
    return {"deleted": enquiry_id}
