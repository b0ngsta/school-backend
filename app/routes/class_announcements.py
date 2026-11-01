"""Class announcements — teachers post to the classes they teach (managers:
any class); parents read them at /student/class-announcements.

- GET    /class-announcements/targets      sections the caller may post to
- GET    /class-announcements              teachers: their own · managers: all (?section_id=)
- POST   /class-announcements              {title, body, section_ids}
- GET    /class-announcements/{id}
- PUT    /class-announcements/{id}         {title, body} (author or managers)
- DELETE /class-announcements/{id}
- POST   /class-announcements/{id}/attachment   add / replace the PDF or photo
- DELETE /class-announcements/{id}/attachment
"""
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.class_announcements import ClassAnnouncementCreate, ClassAnnouncementUpdate
from app.services import class_announcements as service
from app.services.pagination import Page

router = APIRouter(prefix="/class-announcements", tags=["class announcements"])

staff = require_roles(*STAFF)


@router.get("/targets")
def post_targets(user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return service.targets(db, user)


@router.get("")
def list_announcements(response: Response, section_id: int | None = Query(default=None),
                       page: Page = Depends(page_params), user: CurrentUser = Depends(staff),
                       db: Db = Depends(get_db)):
    return paged(response, service.list_staff(db, user, page, section_id))


@router.post("", status_code=201)
def create_announcement(body: ClassAnnouncementCreate, user: CurrentUser = Depends(staff),
                        db: Db = Depends(get_db)):
    return service.create(db, user, body)


@router.get("/{announcement_id}")
def get_announcement(announcement_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return service.get(db, user, announcement_id)


@router.put("/{announcement_id}")
def update_announcement(announcement_id: int, body: ClassAnnouncementUpdate, user: CurrentUser = Depends(staff),
                        db: Db = Depends(get_db)):
    return service.update(db, user, announcement_id, body)


@router.delete("/{announcement_id}", status_code=204)
def delete_announcement(announcement_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    service.delete(db, user, announcement_id)
    return Response(status_code=204)


@router.post("/{announcement_id}/attachment")
def attach_file(announcement_id: int, file: UploadFile = File(...), user: CurrentUser = Depends(staff),
                db: Db = Depends(get_db)):
    return service.attach(db, user, announcement_id, file)


@router.delete("/{announcement_id}/attachment", status_code=204)
def remove_attachment(announcement_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    service.remove_attachment(db, user, announcement_id)
    return Response(status_code=204)
