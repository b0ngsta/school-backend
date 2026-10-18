"""Classes & sections — managed by admin/principal/sub_admin/coordinator; readable by staff."""
from fastapi import APIRouter, Depends

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.classes import ClassCreate, SectionCreate, SubjectsUpdate
from app.services import classes as class_service

router = APIRouter(prefix="/classes", tags=["classes"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)


@router.post("", status_code=201)
def create_class(body: ClassCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return class_service.create_class(db, body)


@router.put("/{class_id}/subjects")
def update_subjects(class_id: int, body: SubjectsUpdate, actor: CurrentUser = Depends(manager),
                    db: Db = Depends(get_db)):
    return class_service.update_subjects(db, class_id, body)


@router.get("")
def list_classes(user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Classes with sections; each section carries student count + class teacher name."""
    return class_service.list_classes(db)


@router.delete("/{class_id}")
def delete_class(class_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return class_service.delete_class(db, class_id)


@router.post("/{class_id}/sections", status_code=201)
def create_section(class_id: int, body: SectionCreate, actor: CurrentUser = Depends(manager),
                   db: Db = Depends(get_db)):
    return class_service.create_section(db, class_id, body)


@router.delete("/{class_id}/sections/{section_id}")
def delete_section(class_id: int, section_id: int, actor: CurrentUser = Depends(manager),
                   db: Db = Depends(get_db)):
    return class_service.delete_section(db, class_id, section_id)
