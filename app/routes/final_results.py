"""Exam format, grading system and final result (migration 0021).

- GET  /exam-format?class_id=        staff: the class's exams of the final result (+ exams not on the list)
- PUT  /exam-format                  managers: {class_id, parts: [{exam_id?, name, max_marks, start_date?, end_date?}]}
- POST /exam-format/copy             managers: {from_class_id, to_class_ids}
- GET  /grading                      any signed-in user: {custom, bands}
- PUT  /grading                      managers: {bands: [{grade, min_percent, max_percent}]} ([] = default scale)
- GET  /final-results?section_id=    staff who can view the section
- GET  /final-results/template       Excel, saved marks prefilled (class teacher / managers; format=json for the app)
- POST /final-results/import         multipart section_id + file (class teacher / managers); all or nothing
- PUT  /final-results/publish        {section_id, published}: show the final result to parents (or hide it)
- GET  /student/final-result         the parent's view ({published: false} until published)
"""
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_current_user, get_db, require_roles
from app.models.auth import CurrentUser
from app.models.exams import FinalPublish, FormatCopy, FormatSave, GradingUpdate
from app.services import exam_format, final_results, grading, spreadsheet
from app.storage.base import content_disposition

router = APIRouter(tags=["exam format & final result"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)
parent = require_roles("student")


@router.get("/exam-format")
def get_format(class_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return exam_format.get(db, class_id)


@router.put("/exam-format")
def save_format(body: FormatSave, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return exam_format.save(db, actor, body)


@router.post("/exam-format/copy")
def copy_format(body: FormatCopy, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return exam_format.copy(db, actor, body)


@router.get("/grading")
def get_grading(user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    return grading.get(db)


@router.put("/grading")
def save_grading(body: GradingUpdate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return grading.update(db, actor, body)


@router.get("/final-results")
def final_sheet(section_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return final_results.sheet(db, user, section_id)


@router.get("/final-results/template")
def final_template(section_id: int, format: str = Query("xlsx", pattern="^(xlsx|json)$"),
                   user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    if format == "json":
        return final_results.template_base64(db, user, section_id)
    filename, content = final_results.template(db, user, section_id)
    return Response(content, media_type=spreadsheet.XLSX_MIME,
                    headers={"Content-Disposition": content_disposition(filename, inline=False)})


@router.post("/final-results/import")
def final_import(section_id: int = Form(...), file: UploadFile = File(...),
                 user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return final_results.import_results(db, user, section_id, file)


@router.put("/final-results/publish")
def final_publish(body: FinalPublish, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return final_results.set_published(db, user, body)


@router.get("/student/final-result")
def my_final_result(user: CurrentUser = Depends(parent), db: Db = Depends(get_db)):
    return final_results.for_student(db, user.id)
