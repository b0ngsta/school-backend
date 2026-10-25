"""School documents shared with parents: timetables and syllabus (PDF or photo).

- GET    /documents?kind=&class_id=&section_id=   staff (teachers: school-wide + their classes)
- POST   /documents            multipart: kind, title, note?, class_id?, section_id?, subject?, file
- PUT    /documents/{id}       multipart: title?, note?, subject?, file? (replace)
- DELETE /documents/{id}

Upload rules are in services/documents.py. Students read theirs from
/student/documents and /student/syllabus.
"""
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.services import documents as service
from app.services.pagination import Page

router = APIRouter(prefix="/documents", tags=["documents"])

staff = require_roles(*STAFF)


@router.get("")
def list_documents(response: Response, kind: str | None = None, class_id: int | None = None,
                   section_id: int | None = None, page: Page = Depends(page_params),
                   user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return paged(response, service.list_documents(db, user, kind, class_id, section_id, page))


@router.post("", status_code=201)
def create_document(
    kind: str = Form(...),
    title: str = Form(...),
    note: str | None = Form(None),
    class_id: int | None = Form(None),
    section_id: int | None = Form(None),
    subject: str | None = Form(None),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    return service.create(db, user, kind, title, note, class_id, section_id, subject, file)


@router.put("/{document_id}")
def update_document(
    document_id: int,
    title: str | None = Form(None),
    note: str | None = Form(None),
    subject: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    return service.update(db, user, document_id, title, note, subject, file)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    service.delete(db, user, document_id)
    return Response(status_code=204)
