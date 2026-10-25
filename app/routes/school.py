"""The school's profile (School Info screen in the apps).

- GET    /school        any signed-in user (students too)
- PUT    /school        admin / principal (name + code are set by the platform operator)
- POST   /school/logo   admin / principal (JPEG / PNG / WebP, 5 MB)
- DELETE /school/logo   admin / principal
"""
from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.db import Db
from app.dependencies import get_current_user, get_db, require_roles
from app.models.auth import CurrentUser
from app.models.school import SchoolProfileUpdate
from app.services import school_profile

router = APIRouter(prefix="/school", tags=["school"])

editor = require_roles("admin", "principal")


@router.get("")
def get_school(user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    return school_profile.get_profile(db)


@router.put("")
def update_school(body: SchoolProfileUpdate, actor: CurrentUser = Depends(editor), db: Db = Depends(get_db)):
    return school_profile.update_profile(db, actor, body)


@router.post("/logo")
def upload_logo(file: UploadFile = File(...), actor: CurrentUser = Depends(editor), db: Db = Depends(get_db)):
    return school_profile.upload_logo(db, actor, file)


@router.delete("/logo", status_code=204)
def delete_logo(actor: CurrentUser = Depends(editor), db: Db = Depends(get_db)):
    school_profile.delete_logo(db, actor)
    return Response(status_code=204)
