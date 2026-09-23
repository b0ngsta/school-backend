"""Teacher <-> class/section assignments (class_teacher | subject_teacher)."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import get_current_user, require_roles
from app.permissions import MANAGERS, get_assignments
from app.schemas import AssignmentCreate

router = APIRouter(prefix="/assignments", tags=["assignments"])

manager = require_roles(*MANAGERS)


@router.post("", status_code=201)
def create_assignment(body: AssignmentCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE id = %s AND user_type = 'teacher'",
            (body.teacher_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Teacher not found")
        cur.execute(
            "SELECT id FROM sections WHERE id = %s AND class_id = %s",
            (body.section_id, body.class_id),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found in that class")

        if body.role == "class_teacher":
            # a teacher can be class teacher of at most ONE section
            cur.execute(
                "SELECT id FROM teacher_assignments "
                "WHERE teacher_id = %s AND role = 'class_teacher'",
                (body.teacher_id,),
            )
            if cur.fetchone():
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This teacher is already a class teacher of another section",
                )
            # a section can have at most ONE class teacher
            cur.execute(
                "SELECT id FROM teacher_assignments "
                "WHERE section_id = %s AND role = 'class_teacher'",
                (body.section_id,),
            )
            if cur.fetchone():
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "This section already has a class teacher"
                )

        cur.execute(
            "SELECT id FROM teacher_assignments "
            "WHERE teacher_id = %s AND section_id = %s AND role = %s "
            "AND (subject <=> %s)",
            (body.teacher_id, body.section_id, body.role, body.subject),
        )
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Assignment already exists")

        cur.execute(
            "INSERT INTO teacher_assignments (teacher_id, class_id, section_id, role, subject) "
            "VALUES (%s, %s, %s, %s, %s)",
            (body.teacher_id, body.class_id, body.section_id, body.role, body.subject),
        )
        return {"id": cur.lastrowid}


@router.get("")
def list_assignments(
    teacher_id: int | None = None,
    section_id: int | None = None,
    actor: dict = Depends(manager),
):
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
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY c.name, s.name", tuple(params))
        return cur.fetchall()


@router.get("/mine")
def my_assignments(user: dict = Depends(require_roles("teacher"))):
    with db_cursor() as cur:
        return get_assignments(cur, user["id"])


@router.delete("/{assignment_id}")
def delete_assignment(assignment_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM teacher_assignments WHERE id = %s", (assignment_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    return {"deleted": assignment_id}
