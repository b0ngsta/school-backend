"""Teacher <-> class/section assignments (class_teacher | subject_teacher)."""
from __future__ import annotations

from app.core.errors import Conflict, NotFound
from app.db import Db
from app.models.assignments import AssignmentCreate
from app.models.auth import CurrentUser
from app.services import access, audit


def create_assignment(db: Db, actor: CurrentUser, body: AssignmentCreate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM users WHERE id = %s AND user_type = 'teacher' FOR UPDATE",
                      (body.teacher_id,)):
            raise NotFound("Teacher not found")
        if not db.one("SELECT id FROM sections WHERE id = %s AND class_id = %s FOR UPDATE",
                      (body.section_id, body.class_id)):
            raise NotFound("Section not found in that class")

        if body.role == "class_teacher":
            # a teacher can be class teacher of at most ONE section
            if db.one("SELECT id FROM teacher_assignments WHERE teacher_id = %s AND role = 'class_teacher'",
                      (body.teacher_id,)):
                raise Conflict("This teacher is already a class teacher of another section")
            # a section can have at most ONE class teacher
            if db.one("SELECT id FROM teacher_assignments WHERE section_id = %s AND role = 'class_teacher'",
                      (body.section_id,)):
                raise Conflict("This section already has a class teacher")

        if db.one(
            "SELECT id FROM teacher_assignments "
            "WHERE teacher_id = %s AND section_id = %s AND role = %s AND (subject <=> %s)",
            (body.teacher_id, body.section_id, body.role, body.subject),
        ):
            raise Conflict("Assignment already exists")

        assignment_id = db.execute(
            "INSERT INTO teacher_assignments (teacher_id, class_id, section_id, role, subject) "
            "VALUES (%s, %s, %s, %s, %s)",
            (body.teacher_id, body.class_id, body.section_id, body.role, body.subject),
        ).lastrowid
        audit.record(db, actor, "assignment.create", "teacher_assignment", assignment_id,
                     body.model_dump())
    return {"id": assignment_id}


def list_assignments(db: Db, teacher_id: int | None, section_id: int | None) -> list[dict]:
    sql = (
        "SELECT ta.*, u.full_name AS teacher_name, c.name AS class_name, s.name AS section_name "
        "FROM teacher_assignments ta "
        "JOIN users u ON u.id = ta.teacher_id "
        "JOIN classes c ON c.id = ta.class_id "
        "JOIN sections s ON s.id = ta.section_id"
    )
    where, params = [], []
    if teacher_id is not None:
        where.append("ta.teacher_id = %s")
        params.append(teacher_id)
    if section_id is not None:
        where.append("ta.section_id = %s")
        params.append(section_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.all(sql + " ORDER BY c.name, s.name", params)


def my_assignments(db: Db, teacher: CurrentUser) -> list[dict]:
    return access.get_assignments(db, teacher.id)


def delete_assignment(db: Db, actor: CurrentUser, assignment_id: int) -> dict:
    with db.transaction():
        if db.execute("DELETE FROM teacher_assignments WHERE id = %s", (assignment_id,)).rowcount == 0:
            raise NotFound("Assignment not found")
        audit.record(db, actor, "assignment.delete", "teacher_assignment", assignment_id)
    return {"deleted": assignment_id}
