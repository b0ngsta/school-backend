"""Notices — posted by admin/principal, visible to all staff."""
from fastapi import APIRouter, Depends, Response

from app.core.roles import NON_STUDENT
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.notices import NoticeCreate
from app.services import notices as notice_service
from app.services.pagination import Page

router = APIRouter(prefix="/notices", tags=["notices"])

poster = require_roles("admin", "principal")
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
