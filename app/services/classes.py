"""Classes, sections and class subjects."""
from __future__ import annotations

from app.core.errors import Conflict, DuplicateKey, NotFound
from app.db import Db
from app.models.classes import ClassCreate, SectionCreate, SubjectsUpdate


def _clean_subjects(subjects: list[str]) -> list[str]:
    """Trim, dedupe (case-insensitive), keep order."""
    seen, out = set(), []
    for s in subjects:
        name = (s or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name[:100])
    return out


def _set_subjects(db: Db, class_id: int, subjects: list[str]) -> None:
    db.execute("DELETE FROM class_subjects WHERE class_id = %s", (class_id,))
    db.execute_many(
        "INSERT INTO class_subjects (class_id, name) VALUES (%s, %s)",
        [(class_id, name) for name in _clean_subjects(subjects)],
    )


def create_class(db: Db, body: ClassCreate) -> dict:
    with db.transaction():
        if db.one("SELECT id FROM classes WHERE name = %s", (body.name,)):
            raise Conflict("Class already exists")
        try:
            class_id = db.execute(
                "INSERT INTO classes (name, fee_amount) VALUES (%s, %s)", (body.name, body.fee_amount)
            ).lastrowid
        except DuplicateKey:
            raise Conflict("Class already exists") from None
        if body.subjects:
            _set_subjects(db, class_id, body.subjects)
    return {"id": class_id, "name": body.name}


def update_subjects(db: Db, class_id: int, body: SubjectsUpdate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM classes WHERE id = %s FOR UPDATE", (class_id,)):
            raise NotFound("Class not found")
        _set_subjects(db, class_id, body.subjects)
        rows = db.all("SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id", (class_id,))
    return {"subjects": [r["name"] for r in rows]}


def list_classes(db: Db) -> list[dict]:
    """Classes with sections; each section carries student count + class teacher name."""
    classes = db.all("SELECT * FROM classes ORDER BY name")
    sections = db.all(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.section_id = s.id) AS student_count, "
        "(SELECT u.full_name FROM teacher_assignments ta JOIN users u ON u.id = ta.teacher_id "
        " WHERE ta.section_id = s.id AND ta.role = 'class_teacher' LIMIT 1) AS class_teacher "
        "FROM sections s ORDER BY s.class_id, s.name"
    )
    subjects = db.all("SELECT class_id, name FROM class_subjects ORDER BY class_id, id")

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


def delete_class(db: Db, class_id: int) -> dict:
    if db.execute("DELETE FROM classes WHERE id = %s", (class_id,)).rowcount == 0:
        raise NotFound("Class not found")
    return {"deleted": class_id}


def create_section(db: Db, class_id: int, body: SectionCreate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM classes WHERE id = %s", (class_id,)):
            raise NotFound("Class not found")
        if db.one("SELECT id FROM sections WHERE class_id = %s AND name = %s", (class_id, body.name)):
            raise Conflict("Section already exists")
        try:
            section_id = db.execute(
                "INSERT INTO sections (class_id, name) VALUES (%s, %s)", (class_id, body.name)
            ).lastrowid
        except DuplicateKey:
            raise Conflict("Section already exists") from None
    return {"id": section_id, "class_id": class_id, "name": body.name}


def delete_section(db: Db, class_id: int, section_id: int) -> dict:
    if db.execute("DELETE FROM sections WHERE id = %s AND class_id = %s",
                  (section_id, class_id)).rowcount == 0:
        raise NotFound("Section not found")
    return {"deleted": section_id}
