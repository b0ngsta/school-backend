"""Messages to parents — managers and class teachers send; parents read
them in the app (/student/messages).

- GET    /messages                    sent messages (managers: all; teachers: their own)
- POST   /messages                    send (JSON; exactly one target)
- GET    /messages/{id}
- GET    /messages/{id}/recipients    who received it, with read time
- POST   /messages/{id}/attachment    add / replace a PDF or photo
- DELETE /messages/{id}
"""
from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.messages import MessageCreate
from app.services import messages as service
from app.services.pagination import Page

router = APIRouter(prefix="/messages", tags=["messages"])

staff = require_roles(*STAFF)


@router.get("")
def list_messages(response: Response, page: Page = Depends(page_params), user: CurrentUser = Depends(staff),
                  db: Db = Depends(get_db)):
    return paged(response, service.list_sent(db, user, page))


@router.post("", status_code=201)
def send_message(body: MessageCreate, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return service.send(db, user, body)


@router.get("/{message_id}")
def get_message(message_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return service.get(db, user, message_id)


@router.get("/{message_id}/recipients")
def message_recipients(message_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return service.recipients(db, user, message_id)


@router.post("/{message_id}/attachment")
def attach_file(message_id: int, file: UploadFile = File(...), user: CurrentUser = Depends(staff),
                db: Db = Depends(get_db)):
    return service.attach(db, user, message_id, file)


@router.delete("/{message_id}", status_code=204)
def delete_message(message_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    service.delete(db, user, message_id)
    return Response(status_code=204)
