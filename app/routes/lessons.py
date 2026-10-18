"""Lesson plans — teachers upload for their assigned classes; admins see everything."""
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.services import lessons as lesson_service
from app.services.pagination import Page

router = APIRouter(prefix="/lessons", tags=["lesson plans"])

staff = require_roles(*STAFF)


@router.post("", status_code=201)
def create_lesson_plan(
    class_id: int = Form(...),
    heading: str = Form(...),
    section_id: int | None = Form(None),
    duration_start: str | None = Form(None),  # YYYY-MM-DD
    duration_end: str | None = Form(None),
    final_remark: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    teacher: CurrentUser = Depends(require_roles("teacher")),
    db: Db = Depends(get_db),
):
    return lesson_service.create_lesson_plan(
        db, teacher, class_id=class_id, heading=heading, section_id=section_id,
        duration_start=duration_start, duration_end=duration_end,
        final_remark=final_remark, files=files,
    )


@router.get("")
def list_lesson_plans(
    response: Response,
    teacher_id: int | None = None,
    class_id: int | None = None,
    page: Page = Depends(page_params),
    user: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    """Managers see all (filterable); teachers see their own."""
    return paged(response, lesson_service.list_lesson_plans(db, user, teacher_id, class_id, page))


@router.get("/files/{file_id}")
def download_lesson_file(file_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return lesson_service.download_lesson_file(db, file_id)


@router.delete("/{plan_id}")
def delete_lesson_plan(plan_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Teacher: own plans. Admin/principal: any."""
    return lesson_service.delete_lesson_plan(db, user, plan_id)
