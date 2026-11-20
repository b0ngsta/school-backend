"""Report-card PDFs released to parents (migration 0028). MANAGERS only.

- POST   /report-card-releases       multipart: source (exam|final), exam_id?, title, students (JSON list of
                                     student ids in print order), sides (1|2), file (the class saved as ONE PDF),
                                     front_template_id?, back_template_id?, page_w_mm?, page_h_mm?, notify?
                                     → cut into one PDF per student, stored, parents notified
- GET    /report-card-releases       one line per released result (newest first)
- DELETE /report-card-releases?result_key=exam:12   withdraw a whole release
- GET    /report-card-files?exam_id=|session=|result_key=&class_id=   the stored files
- DELETE /report-card-files/{id}     withdraw one student's file

Parents: GET /student/report-card-files (routes/student_portal.py).
Staff: GET /students/{id}/report-card-files (routes/students.py).
"""
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.services import report_card_files as service

manager = require_roles(*MANAGERS)

router = APIRouter(tags=["report cards"])


@router.post("/report-card-releases", status_code=201)
def release(
    source: str = Form(..., pattern="^(exam|final)$"),
    title: str = Form(..., max_length=160),
    students: str = Form(...),
    sides: int = Form(2, ge=1, le=2),
    exam_id: int | None = Form(None),
    front_template_id: int | None = Form(None),
    back_template_id: int | None = Form(None),
    page_w_mm: float | None = Form(None, gt=0, le=2000),
    page_h_mm: float | None = Form(None, gt=0, le=2000),
    notify: bool = Form(True),
    file: UploadFile = File(...),
    actor: CurrentUser = Depends(manager),
    db: Db = Depends(get_db),
):
    return service.release(db, actor, source=source, exam_id=exam_id, title=title, students_raw=students,
                           sides=sides, file=file, front_template_id=front_template_id,
                           back_template_id=back_template_id, page_w_mm=page_w_mm, page_h_mm=page_h_mm,
                           send_notice=notify)


@router.get("/report-card-releases")
def releases(user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.summary(db)


@router.delete("/report-card-releases")
def withdraw_release(result_key: str = Query(..., pattern=r"^(exam:\d+|final:\d{4}(-\d{4})?)$"),
                     actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return {"withdrawn": service.withdraw_result(db, actor, result_key)}


@router.get("/report-card-files")
def files(exam_id: int | None = None, session: str | None = Query(None, pattern=r"^\d{4}(-\d{4})?$"),
          result_key: str | None = Query(None, pattern=r"^(exam:\d+|final:\d{4}(-\d{4})?)$"),
          class_id: int | None = None, user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.list_files(db, result_key=result_key, exam_id=exam_id, session=session, class_id=class_id)


@router.delete("/report-card-files/{file_id}", status_code=204)
def withdraw(file_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    service.withdraw(db, actor, file_id)
    return Response(status_code=204)
