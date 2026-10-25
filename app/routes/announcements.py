"""App-launch announcements (popup in the student app) — managers.

The student app reads the active ones from GET /student/announcements.
"""
from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.announcements import AnnouncementSave
from app.models.auth import CurrentUser
from app.services import announcements as service
from app.services.pagination import Page

router = APIRouter(prefix="/announcements", tags=["announcements"])

manager = require_roles(*MANAGERS)


@router.get("")
def list_announcements(response: Response, page: Page = Depends(page_params),
                       actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, service.list_all(db, page))


@router.post("", status_code=201)
def create_announcement(body: AnnouncementSave, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.create(db, actor, body)


@router.put("/{announcement_id}")
def update_announcement(announcement_id: int, body: AnnouncementSave, actor: CurrentUser = Depends(manager),
                        db: Db = Depends(get_db)):
    return service.update(db, actor, announcement_id, body)


@router.delete("/{announcement_id}", status_code=204)
def delete_announcement(announcement_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    service.delete(db, actor, announcement_id)
    return Response(status_code=204)


@router.post("/{announcement_id}/images", status_code=201)
def add_image(announcement_id: int, file: UploadFile = File(...), actor: CurrentUser = Depends(manager),
              db: Db = Depends(get_db)):
    return service.add_image(db, actor, announcement_id, file)


@router.delete("/{announcement_id}/images/{image_id}", status_code=204)
def delete_image(announcement_id: int, image_id: int, actor: CurrentUser = Depends(manager),
                 db: Db = Depends(get_db)):
    service.delete_image(db, actor, announcement_id, image_id)
    return Response(status_code=204)
