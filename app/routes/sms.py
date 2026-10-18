"""Bulk SMS — compose + history (managers only). Sending is simulated."""
from fastapi import APIRouter, Depends, Response

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.sms import SmsCreate
from app.services import sms as sms_service
from app.services.pagination import Page

router = APIRouter(prefix="/sms", tags=["bulk sms"])

manager = require_roles(*MANAGERS)


@router.get("")
def sms_history(response: Response, page: Page = Depends(page_params),
                actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, sms_service.history(db, page))


@router.post("", status_code=201)
def send_sms(body: SmsCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return sms_service.send(db, actor, body)
