"""Classes & sections — managed by admin/principal/sub_admin; readable by staff."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS, STAFF
from app.schemas import ClassCreate, SectionCreate, SubjectsUpdate

router = APIRouter(prefix="/classes", tags=["classes"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)


def _clean_subjects(subjects: list[str]) -> list[str]:
    """Trim, dedupe (case-insensitive), keep order."""
    seen, out = set(), []
    for s in subjects:
        name = (s or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name[:100])
    return out


def _set_subjects(cur, class_id: int, subjects: list[str]):
    cur.execute("DELETE FROM class_subjects WHERE class_id = %s", (class_id,))
    for name in _clean_subjects(subjects):
        cur.execute(
            "INSERT INTO class_subjects (class_id, name) VALUES (%s, %s)",
            (class_id, name),
        )


@router.post("", status_code=201)
def create_class(body: ClassCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM classes WHERE name = %s", (body.name,))
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Class already exists")
        cur.execute(
            "INSERT INTO classes (name, fee_amount) VALUES (%s, %s)",
            (body.name, body.fee_amount),
        )
        class_id = cur.lastrowid
        if body.subjects:
            _set_subjects(cur, class_id, body.subjects)
        return {"id": class_id, "name": body.name}


@router.put("/{class_id}/subjects")
def update_subjects(class_id: int, body: SubjectsUpdate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM classes WHERE id = %s", (class_id,))
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not found")
        _set_subjects(cur, class_id, body.subjects)
        cur.execute(
            "SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id", (class_id,)
        )
        return {"subjects": [r["name"] for r in cur.fetchall()]}


@router.get("")
def list_classes(user: dict = Depends(staff)):
    """Classes with sections; each section carries student count + class teacher name."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM classes ORDER BY name")
        classes = cur.fetchall()
        cur.execute(
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.section_id = s.id) AS student_count, "
            "(SELECT u.full_name FROM teacher_assignments ta JOIN users u ON u.id = ta.teacher_id "
            " WHERE ta.section_id = s.id AND ta.role = 'class_teacher' LIMIT 1) AS class_teacher "
            "FROM sections s ORDER BY s.class_id, s.name"
        )
        sections = cur.fetchall()
        cur.execute("SELECT class_id, name FROM class_subjects ORDER BY class_id, id")
        subjects = cur.fetchall()
    by_class: dict[int, list] = {}
    for s in sections:
        by_class.setdefault(s["class_id"], []).append(s)
    subj_by_class: dict[int, list] = {}
    for s in subjects:
        subj_by_class.setdefault(s["class_id"], []).append(s["name"])
    for c in classes:
        c["sections"] = by_class.get(c["id"], [])
        c["subjects"] = subj_by_class.get(c["id"], [])
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
