"""Homework for a whole class-section.

- /homework                      list (by class/section) and assign
- /homework/{id}                 edit / delete (the teacher who set it, or managers)
- /homework/{id}/submissions     every student of the section with their status; bulk update

Permissions: managers, or teachers assigned to the section (class or subject teacher).
"""
from fastapi import APIRouter, Depends, Response

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.homework import ClassHomeworkCreate, ClassHomeworkUpdate, SubmissionsUpdate
from app.services import homework as homework_service
from app.services.pagination import Page

router = APIRouter(prefix="/homework", tags=["homework"])

staff = require_roles(*STAFF)


@router.get("")
def list_homework(response: Response, class_id: int | None = None, section_id: int | None = None,
                  page: Page = Depends(page_params), user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return paged(response, homework_service.list_homework(db, user, class_id, section_id, page))


@router.post("", status_code=201)
def create_homework(body: ClassHomeworkCreate, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Assign to every student of the section (they are notified)."""
    return homework_service.create_homework(db, user, body)


@router.put("/{homework_id}")
def update_homework(homework_id: int, body: ClassHomeworkUpdate, user: CurrentUser = Depends(staff),
                    db: Db = Depends(get_db)):
    return homework_service.update_homework(db, user, homework_id, body)


@router.delete("/{homework_id}", status_code=204)
def delete_homework(homework_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    homework_service.delete_homework(db, user, homework_id)
    return Response(status_code=204)


@router.get("/{homework_id}/submissions")
def list_submissions(homework_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return homework_service.submissions(db, user, homework_id)


@router.put("/{homework_id}/submissions", status_code=204)
def save_submissions(homework_id: int, body: SubmissionsUpdate, user: CurrentUser = Depends(staff),
                     db: Db = Depends(get_db)):
    homework_service.save_submissions(db, user, homework_id, body)
    return Response(status_code=204)
