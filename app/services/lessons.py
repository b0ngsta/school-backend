"""Lesson plans — teachers upload for their assigned classes; admins see everything."""
from __future__ import annotations

from datetime import date

from fastapi import UploadFile
from fastapi.responses import FileResponse

from app.core.errors import BadRequest, Forbidden, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.services import access, pagination, storage

ALLOWED_TYPES = storage.IMAGE_TYPES | storage.PDF_TYPES
MAX_FILE_MB = 10


def _optional_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{field} must be a date (YYYY-MM-DD)") from None


def create_lesson_plan(
    db: Db,
    teacher: CurrentUser,
    *,
    class_id: int,
    heading: str,
    section_id: int | None,
    duration_start: str | None,
    duration_end: str | None,
    final_remark: str | None,
    files: list[UploadFile],
) -> dict:
    start, end = _optional_date(duration_start, "duration_start"), _optional_date(duration_end, "duration_end")
    with db.transaction():
        if not access.is_assigned(db, teacher, class_id, section_id):
            raise Forbidden("You are not assigned to this class/section")
        plan_id = db.execute(
            "INSERT INTO lesson_plans "
            "(org_id, teacher_id, class_id, section_id, heading, duration_start, duration_end, final_remark) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, teacher.id, class_id, section_id, heading, start, end, final_remark),
        ).lastrowid
        saved = []
        for upload in files:
            stored = storage.save_upload(
                db, upload, "lessons", ALLOWED_TYPES, max_mb=MAX_FILE_MB,
                type_error=f"Only photos/PDF allowed, got {upload.content_type}",
            )
            file_id = db.execute(
                "INSERT INTO lesson_files (lesson_plan_id, file_path, original_name, content_type) "
                "VALUES (%s, %s, %s, %s)",
                (plan_id, stored.path, stored.original_name or "file", stored.content_type),
            ).lastrowid
            saved.append({"id": file_id, "original_name": upload.filename})
    return {"id": plan_id, "files": saved}


def list_lesson_plans(db: Db, user: CurrentUser, teacher_id: int | None, class_id: int | None,
                      page: pagination.Page | None = None) -> pagination.PageResult:
    """Managers see all (filterable); teachers see their own."""
    where, params = ["lp.org_id = %s"], [db.org]
    if user.user_type == "teacher":
        where.append("lp.teacher_id = %s")
        params.append(user.id)
    elif teacher_id is not None:
        where.append("lp.teacher_id = %s")
        params.append(teacher_id)
    if class_id is not None:
        where.append("lp.class_id = %s")
        params.append(class_id)

    sql = (
        "SELECT lp.*, u.full_name AS teacher_name, c.name AS class_name, s.name AS section_name "
        "FROM lesson_plans lp "
        "JOIN users u ON u.id = lp.teacher_id "
        "JOIN classes c ON c.id = lp.class_id "
        "LEFT JOIN sections s ON s.id = lp.section_id"
    )
    sql += " WHERE " + " AND ".join(where)
    result = pagination.fetch(db, sql, params, "lp.created_at DESC, lp.id DESC", page)
    plans = result.rows

    if plans:
        ids = [p["id"] for p in plans]
        files = db.all(
            "SELECT id, lesson_plan_id, original_name, content_type FROM lesson_files "
            f"WHERE lesson_plan_id IN ({','.join(['%s'] * len(ids))})",
            ids,
        )
        by_plan: dict[int, list] = {}
        for f in files:
            by_plan.setdefault(f["lesson_plan_id"], []).append(f)
        for p in plans:
            p["files"] = by_plan.get(p["id"], [])
    return result


def download_lesson_file(db: Db, file_id: int) -> FileResponse:
    f = db.one("SELECT f.* FROM lesson_files f JOIN lesson_plans lp ON lp.id = f.lesson_plan_id "
               "WHERE f.id = %s AND lp.org_id = %s", (file_id, db.org))
    if not f:
        raise NotFound("File not found")
    return storage.file_response(f["file_path"], media_type=f["content_type"],
                                 filename=f["original_name"], inline=False)


def delete_lesson_plan(db: Db, user: CurrentUser, plan_id: int) -> dict:
    """Teacher: own plans. Admin/principal/coordinator: any. Sub-admin: none."""
    with db.transaction():
        plan = db.one("SELECT teacher_id FROM lesson_plans WHERE id = %s AND org_id = %s FOR UPDATE",
                      (plan_id, db.org))
        if not plan:
            raise NotFound("Lesson plan not found")
        if user.user_type == "teacher" and plan["teacher_id"] != user.id:
            raise Forbidden("Not your lesson plan")
        if user.user_type == "sub_admin":
            raise Forbidden("Not allowed")
        paths = [r["file_path"] for r in
                 db.all("SELECT file_path FROM lesson_files WHERE lesson_plan_id = %s", (plan_id,))]
        db.execute("DELETE FROM lesson_plans WHERE id = %s", (plan_id,))
        for path in paths:
            storage.delete_after_commit(db, path)
    return {"deleted": plan_id}
