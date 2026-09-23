"""Classes & sections — managed by sub_admin/admin, readable by any logged-in user."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import get_current_user, require_roles
from app.schemas import ClassCreate, SectionCreate

router = APIRouter(prefix="/classes", tags=["classes"])

manager = require_roles("admin", "sub_admin")


@router.post("", status_code=201)
def create_class(body: ClassCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM classes WHERE name = %s", (body.name,))
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Class already exists")
        cur.execute("INSERT INTO classes (name) VALUES (%s)", (body.name,))
        return {"id": cur.lastrowid, "name": body.name}


@router.get("")
def list_classes(user: dict = Depends(get_current_user)):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM classes ORDER BY name")
        classes = cur.fetchall()
        cur.execute("SELECT * FROM sections ORDER BY class_id, name")
        sections = cur.fetchall()
    by_class: dict[int, list] = {}
    for s in sections:
        by_class.setdefault(s["class_id"], []).append(s)
    for c in classes:
        c["sections"] = by_class.get(c["id"], [])
    return classes


@router.delete("/{class_id}")
def delete_class(class_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM classes WHERE id = %s", (class_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not found")
    return {"deleted": class_id}


@router.post("/{class_id}/sections", status_code=201)
def create_section(class_id: int, body: SectionCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM classes WHERE id = %s", (class_id,))
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not found")
        cur.execute(
            "SELECT id FROM sections WHERE class_id = %s AND name = %s",
            (class_id, body.name),
        )
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Section already exists")
        cur.execute(
            "INSERT INTO sections (class_id, name) VALUES (%s, %s)",
            (class_id, body.name),
        )
        return {"id": cur.lastrowid, "class_id": class_id, "name": body.name}


@router.delete("/{class_id}/sections/{section_id}")
def delete_section(class_id: int, section_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "DELETE FROM sections WHERE id = %s AND class_id = %s",
            (section_id, class_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found")
    return {"deleted": section_id}
