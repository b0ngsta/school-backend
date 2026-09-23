"""Lesson plans — teachers upload for their assigned classes; admins see everything."""
import os
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.config import UPLOAD_DIR
from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS, STAFF, is_assigned

router = APIRouter(prefix="/lessons", tags=["lesson plans"])

staff = require_roles(*STAFF)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_FILE_MB = 10


def _save_upload(file: UploadFile) -> str:
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Only photos/PDF allowed, got {file.content_type}",
        )
    data = file.file.read()
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Max {MAX_FILE_MB} MB")
    ext = os.path.splitext(file.filename or "")[1] or ".bin"
    folder = os.path.join(UPLOAD_DIR, "lessons")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{secrets.token_hex(8)}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path


@router.post("", status_code=201)
def create_lesson_plan(
    class_id: int = Form(...),
    heading: str = Form(...),
    section_id: int | None = Form(None),
    duration_start: str | None = Form(None),  # YYYY-MM-DD
    duration_end: str | None = Form(None),
    final_remark: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    teacher: dict = Depends(require_roles("teacher")),
):
    with db_cursor() as cur:
        if not is_assigned(cur, teacher, class_id, section_id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "You are not assigned to this class/section"
            )
        cur.execute(
            "INSERT INTO lesson_plans "
            "(teacher_id, class_id, section_id, heading, duration_start, duration_end, final_remark) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                teacher["id"], class_id, section_id, heading,
                duration_start or None, duration_end or None, final_remark,
            ),
        )
        plan_id = cur.lastrowid
        saved = []
        for f in files:
            path = _save_upload(f)
            cur.execute(
                "INSERT INTO lesson_files (lesson_plan_id, file_path, original_name, content_type) "
                "VALUES (%s, %s, %s, %s)",
                (plan_id, path, f.filename, f.content_type),
            )
            saved.append({"id": cur.lastrowid, "original_name": f.filename})
    return {"id": plan_id, "files": saved}


@router.get("")
def list_lesson_plans(
    teacher_id: int | None = None,
    class_id: int | None = None,
    user: dict = Depends(staff),
):
    """Managers see all (filterable); teachers see their own."""
    where, params = [], []
    if user["user_type"] == "teacher":
        where.append("lp.teacher_id = %s")
        params.append(user["id"])
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
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY lp.created_at DESC"

    with db_cursor() as cur:
        cur.execute(sql, tuple(params))
        plans = cur.fetchall()
        if plans:
            ids = tuple(p["id"] for p in plans)
            cur.execute(
                "SELECT id, lesson_plan_id, original_name, content_type FROM lesson_files "
                f"WHERE lesson_plan_id IN ({','.join(['%s'] * len(ids))})",
                ids,
            )
            by_plan: dict[int, list] = {}
            for f in cur.fetchall():
                by_plan.setdefault(f["lesson_plan_id"], []).append(f)
            for p in plans:
                p["files"] = by_plan.get(p["id"], [])
    return plans


@router.get("/files/{file_id}")
def download_lesson_file(file_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM lesson_files WHERE id = %s", (file_id,))
        f = cur.fetchone()
    if not f:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FileResponse(
        f["file_path"], filename=f["original_name"], media_type=f["content_type"]
    )


@router.delete("/{plan_id}")
def delete_lesson_plan(plan_id: int, user: dict = Depends(staff)):
    """Teacher: own plans. Admin/principal: any."""
    with db_cursor() as cur:
        cur.execute("SELECT teacher_id FROM lesson_plans WHERE id = %s", (plan_id,))
        plan = cur.fetchone()
        if not plan:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Lesson plan not found")
        if user["user_type"] == "teacher" and plan["teacher_id"] != user["id"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your lesson plan")
        if user["user_type"] == "sub_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
        cur.execute("SELECT file_path FROM lesson_files WHERE lesson_plan_id = %s", (plan_id,))
        paths = [r["file_path"] for r in cur.fetchall()]
        cur.execute("DELETE FROM lesson_plans WHERE id = %s", (plan_id,))
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass
    return {"deleted": plan_id}
