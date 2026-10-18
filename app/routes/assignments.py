"""Teacher <-> class/section assignments (class_teacher | subject_teacher)."""
from fastapi import APIRouter, Depends

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.assignments import AssignmentCreate
from app.models.auth import CurrentUser
from app.services import assignments as assignment_service

router = APIRouter(prefix="/assignments", tags=["assignments"])

manager = require_roles(*MANAGERS)


@router.post("", status_code=201)
def create_assignment(body: AssignmentCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return assignment_service.create_assignment(db, actor, body)


@router.get("")
def list_assignments(
    teacher_id: int | None = None,
    section_id: int | None = None,
    actor: CurrentUser = Depends(manager),
    db: Db = Depends(get_db),
):
    return assignment_service.list_assignments(db, teacher_id, section_id)


@router.get("/mine")
def my_assignments(user: CurrentUser = Depends(require_roles("teacher")), db: Db = Depends(get_db)):
    return assignment_service.my_assignments(db, user)


@router.delete("/{assignment_id}")
def delete_assignment(assignment_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return assignment_service.delete_assignment(db, actor, assignment_id)
