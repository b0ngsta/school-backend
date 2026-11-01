"""Bulk user import: Excel template, column help, export and upload (.xlsx, CSV still accepted)."""
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.common import UserType
from app.services import spreadsheet, user_import
from app.storage.base import content_disposition

router = APIRouter(prefix="/imports", tags=["bulk import"])

staff = require_roles(*STAFF)


def _xlsx(data: bytes, filename: str) -> Response:
    return Response(data, media_type=spreadsheet.XLSX_MIME,
                    headers={"Content-Disposition": content_disposition(filename, inline=False)})


@router.get("/users/columns")
def import_columns(user_type: UserType, actor: CurrentUser = Depends(staff)):
    """The CSV columns for this account type: name, required, format, example."""
    return user_import.describe(actor, user_type)


@router.get("/users/template")
def import_template(user_type: UserType, actor: CurrentUser = Depends(staff)):
    """An empty Excel sheet with the right headers (+ a "How to fill" sheet)."""
    return _xlsx(*user_import.template(actor, user_type))


@router.get("/users/export")
def export_users(
    user_type: UserType,
    class_id: int | None = Query(None, description="Required for students"),
    section_id: int | None = Query(None, description="Required for students"),
    actor: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    """Current users in the import format, to edit and upload again."""
    return _xlsx(*user_import.export(db, actor, user_type, class_id, section_id))


@router.post("/users")
def import_users(
    user_type: UserType = Form(...),
    class_id: int | None = Form(None),
    section_id: int | None = Form(None),
    dry_run: bool = Form(True, description="true (default): only report what would happen"),
    file: UploadFile = File(..., description="The Excel (.xlsx) file (CSV also accepted)"),
    photos: UploadFile | None = File(None, description="Optional ZIP of profile photos"),
    actor: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    """Create or update users from a CSV. Send it first with dry_run=true to
    show the preview, then again with dry_run=false to save."""
    return user_import.run_import(db, actor, user_type=user_type, class_id=class_id, section_id=section_id,
                                  csv_file=file, photos_file=photos, dry_run=dry_run)
