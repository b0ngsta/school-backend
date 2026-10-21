"""Reception — admission enquiries."""
from __future__ import annotations

from app.core.errors import NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.enquiries import EnquiryCreate, EnquiryUpdate
from app.services import pagination


def stats(db: Db) -> dict:
    row = db.one(
        "SELECT "
        "SUM(DATE(created_at) = CURDATE()) AS today, "
        "SUM(status = 'converted') AS converted, "
        "SUM(status = 'new') AS pending, "
        "SUM(status = 'follow_up') AS follow_ups, "
        "COUNT(*) AS total "
        "FROM enquiries WHERE org_id = %s",
        (db.org,),
    )
    return {k: int(v or 0) for k, v in row.items()}


def list_enquiries(db: Db, status_filter: str | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql, params = "SELECT * FROM enquiries WHERE org_id = %s", (db.org,)
    if status_filter:
        sql += " AND status = %s"
        params = (db.org, status_filter)
    return pagination.fetch(db, sql, params, "created_at DESC, id DESC", page)


def create_enquiry(db: Db, actor: CurrentUser, body: EnquiryCreate) -> dict:
    enquiry_id = db.execute(
        "INSERT INTO enquiries "
        "(org_id, parent_name, student_name, class_interested, contact, email, notes, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (db.org, body.parent_name, body.student_name, body.class_interested,
         body.contact, body.email, body.notes, actor.id),
    ).lastrowid
    return {"id": enquiry_id}


def update_enquiry(db: Db, enquiry_id: int, body: EnquiryUpdate) -> dict:
    with db.transaction():
        current = db.one("SELECT * FROM enquiries WHERE id = %s AND org_id = %s FOR UPDATE", (enquiry_id, db.org))
        if not current:
            raise NotFound("Enquiry not found")
        merged = {**current, **body.model_dump(exclude_unset=True)}
        db.execute(
            "UPDATE enquiries SET parent_name=%s, student_name=%s, class_interested=%s, "
            "contact=%s, email=%s, notes=%s, status=%s WHERE id=%s",
            (merged["parent_name"], merged["student_name"], merged["class_interested"],
             merged["contact"], merged["email"], merged["notes"], merged["status"], enquiry_id),
        )
    return {"updated": enquiry_id}


def delete_enquiry(db: Db, enquiry_id: int) -> dict:
    if db.execute("DELETE FROM enquiries WHERE id = %s AND org_id = %s", (enquiry_id, db.org)).rowcount == 0:
        raise NotFound("Enquiry not found")
    return {"deleted": enquiry_id}
