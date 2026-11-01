"""Exams — schedule, subject-wise question papers, per-student results.

- staff can view exams, sections and results
- results entry: managers + teachers assigned to the section
- question paper upload: admin / sub_admin / coordinator / principal
- exams with class_id = NULL apply to ALL classes
"""
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.exams import MARKS_LIMIT, ExamCreate, ExamUpdate, ResultsSave
from app.services import exam_results_import, spreadsheet
from app.services import exams as exam_service
from app.storage.base import content_disposition

router = APIRouter(prefix="/exams", tags=["exams"])

manager = require_roles(*MANAGERS)
paper_manager = require_roles("admin", "sub_admin", "coordinator", "principal")
staff = require_roles(*STAFF)


@router.get("/stats")
def exam_stats(user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return exam_service.stats(db)


@router.get("")
def list_exams(user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return exam_service.list_exams(db)


@router.post("", status_code=201)
def create_exam(body: ExamCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return exam_service.create_exam(db, body)


@router.get("/{exam_id}")
def exam_detail(exam_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Exam info + the classes it applies to + sections for the results flow."""
    return exam_service.exam_detail(db, exam_id)


@router.put("/{exam_id}")
def update_exam(exam_id: int, body: ExamUpdate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return exam_service.update_exam(db, actor, exam_id, body)


@router.delete("/{exam_id}")
def delete_exam(exam_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return exam_service.delete_exam(db, actor, exam_id)


# ---------- subject-wise question papers ----------

@router.get("/{exam_id}/classes/{class_id}/papers")
def class_papers(exam_id: int, class_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """The class's subjects, each with its uploaded paper (if any)."""
    return exam_service.class_papers(db, exam_id, class_id)


@router.post("/{exam_id}/classes/{class_id}/papers", status_code=201)
def upload_subject_paper(
    exam_id: int,
    class_id: int,
    subject: str = Form(...),
    file: UploadFile = File(...),
    actor: CurrentUser = Depends(paper_manager),
    db: Db = Depends(get_db),
):
    return exam_service.upload_subject_paper(db, actor, exam_id, class_id, subject, file)


@router.get("/{exam_id}/papers/{paper_id}/file")
def get_subject_paper(exam_id: int, paper_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return exam_service.subject_paper_file(db, exam_id, paper_id)


@router.delete("/{exam_id}/papers/{paper_id}")
def delete_subject_paper(exam_id: int, paper_id: int, actor: CurrentUser = Depends(paper_manager),
                         db: Db = Depends(get_db)):
    return exam_service.delete_subject_paper(db, actor, exam_id, paper_id)


# ---------- legacy single question paper (kept for old exams) ----------

@router.post("/{exam_id}/paper")
def upload_paper(exam_id: int, file: UploadFile = File(...), actor: CurrentUser = Depends(paper_manager),
                 db: Db = Depends(get_db)):
    return exam_service.upload_legacy_paper(db, actor, exam_id, file)


@router.get("/{exam_id}/paper")
def get_paper(exam_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return exam_service.legacy_paper_file(db, exam_id)


# ---------- results ----------

@router.get("/{exam_id}/results")
def exam_results(exam_id: int, section_id: int,
                 subject: str | None = Query(None, max_length=100,
                                             description="Leave out for the whole exam; else a subject of the class"),
                 user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return exam_service.exam_results(db, user, exam_id, section_id, subject)


@router.get("/{exam_id}/results/sheet")
def results_sheet(exam_id: int, section_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Every subject's marks for the section in one table (+ totals, % and `can_import`)."""
    return exam_results_import.sheet(db, user, exam_id, section_id)


@router.get("/{exam_id}/results/template")
def results_template(exam_id: int, section_id: int, format: str = Query("xlsx", pattern="^(xlsx|json)$"),
                     user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Excel sheet to fill in (class teacher / managers). `format=json` →
    {filename, mime, content_base64} for the mobile app."""
    if format == "json":
        return exam_results_import.template_base64(db, user, exam_id, section_id)
    filename, content = exam_results_import.template(db, user, exam_id, section_id)
    return Response(content, media_type=spreadsheet.XLSX_MIME,
                    headers={"Content-Disposition": content_disposition(filename, inline=False)})


@router.post("/{exam_id}/results/import")
def import_results(exam_id: int, section_id: int = Form(...),
                   max_marks: float = Form(..., gt=0, lt=MARKS_LIMIT,
                                           description="What every subject in this upload is out of, e.g. 50"),
                   file: UploadFile = File(..., description="The filled-in Excel (.xlsx) template"),
                   user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Upload the whole section's marks from the Excel template (class teacher / managers).
    All or nothing; publishes the exam's results."""
    return exam_results_import.import_results(db, user, exam_id, section_id, file, max_marks)


@router.put("/{exam_id}/results")
def save_results(exam_id: int, body: ResultsSave, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return exam_service.save_results(db, user, exam_id, body)
