"""Staff user management (admin / principal / sub_admin / teacher accounts).

Students are managed via /students (section pages).
"""
from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.users import StaffCreate, StaffUpdate, TeacherInfoUpdate
from app.services import users as user_service
from app.services.pagination import Page

router = APIRouter(prefix="/users", tags=["users"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)


@router.post("", status_code=201)
def create_staff(body: StaffCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return user_service.create_staff(db, actor, body)


@router.get("")
def list_users(response: Response, user_type: str | None = None, page: Page = Depends(page_params),
               actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, user_service.list_users(db, user_type, page))


@router.put("/{user_id}")
def update_staff(user_id: int, body: StaffUpdate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Edit a staff account's name, email or phone (fields left out keep their value)."""
    return user_service.update_staff(db, actor, user_id, body)


@router.delete("/{user_id}")
def delete_user(user_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return user_service.delete_user(db, actor, user_id)


@router.post("/{user_id}/photo")
def upload_photo(
    user_id: int,
    file: UploadFile = File(...),
    actor: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    """Staff photos: managers. Student photos: managers or the class teacher."""
    return user_service.upload_photo(db, actor, user_id, file)


@router.get("/teachers")
def list_teachers(actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return user_service.list_teachers(db)


@router.put("/teachers/{user_id}")
def update_teacher_info(
    user_id: int, body: TeacherInfoUpdate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)
):
    return user_service.update_teacher_info(db, user_id, body)
