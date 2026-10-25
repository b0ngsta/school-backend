"""Notices — posted by managers (admin, principal, sub-admin, coordinator),
visible to all staff and to parents (/student/notices). A notice may carry
one attachment (PDF or photo), e.g. the new timetable."""
from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.core.roles import MANAGERS, NON_STUDENT
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.notices import NoticeCreate
from app.services import notices as notice_service
from app.services.pagination import Page

router = APIRouter(prefix="/notices", tags=["notices"])

poster = require_roles(*MANAGERS)
staff = require_roles(*NON_STUDENT)  # drivers can read notices too


@router.post("", status_code=201)
def create_notice(body: NoticeCreate, actor: CurrentUser = Depends(poster), db: Db = Depends(get_db)):
    return notice_service.create_notice(db, actor, body)


@router.get("")
def list_notices(response: Response, page: Page = Depends(page_params),
                 user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return paged(response, notice_service.list_notices(db, page))


@router.delete("/{notice_id}")
def delete_notice(notice_id: int, actor: CurrentUser = Depends(poster), db: Db = Depends(get_db)):
    return notice_service.delete_notice(db, notice_id)


@router.post("/{notice_id}/attachment")
def attach_file(notice_id: int, file: UploadFile = File(...), actor: CurrentUser = Depends(poster),
                db: Db = Depends(get_db)):
    """Add or replace the notice's PDF / photo."""
    return notice_service.attach(db, actor, notice_id, file)


@router.delete("/{notice_id}/attachment", status_code=204)
def remove_attachment(notice_id: int, actor: CurrentUser = Depends(poster), db: Db = Depends(get_db)):
    notice_service.remove_attachment(db, actor, notice_id)
    return Response(status_code=204)
