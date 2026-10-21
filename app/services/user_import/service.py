"""Bulk user import from a CSV file (plus an optional ZIP of photos), and the
matching template and export.

Who may import what
* admin: every account type
* principal / sub-admin / coordinator: coordinator, teacher, driver, student
  (admin, principal and sub-admin accounts are admin-only, as in the forms)
* teacher: students, only into the section they are CLASS teacher of
* driver / student: nothing

Rows are matched to existing accounts by username: a known username updates
that account (blank cells keep the current value), an unknown one creates
it. A preview (`dry_run`, the default) reports what would happen per row.
Saving writes every row without problems in one transaction; rows with
problems are skipped and reported, so they can be fixed and the same file
uploaded again (rows saved the first time then show as unchanged).
"""
from __future__ import annotations

import re

import pymysql
from fastapi import UploadFile

from app.core.clock import school_today
from app.core.config import settings
from app.core.errors import BadRequest, Conflict, DuplicateKey, Forbidden, NotFound, Unprocessable
from app.core.roles import ADMIN_MANAGED, MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.services import access, audit
from app.services.user_import import csv_io, photos, planner, writer
from app.services.user_import.columns import USER_TYPES, columns_for
from app.services.user_import.photos import PHOTO_MAX_MB, PhotoArchive

_MANAGER_TYPES = ("coordinator", "teacher", "driver", "student")
IMPORTABLE: dict[str, tuple[str, ...]] = {
    "admin": USER_TYPES,
    **{role: _MANAGER_TYPES for role in MANAGERS if role != "admin"},
    "teacher": ("student",),
}

CHANGED_WHILE_SAVING = ("Some of these users were changed by someone else while the file was being saved. "
                        "Nothing was saved; upload the file again to see the current state.")


# ------------------------------------------------------------ permissions

def ensure_can_import_type(actor: CurrentUser, user_type: str) -> None:
    if user_type in IMPORTABLE.get(actor.user_type, ()):
        return
    if actor.user_type == "teacher":
        raise Forbidden("Teachers can only add students, to the section they are class teacher of.")
    if user_type in ADMIN_MANAGED and actor.user_type in MANAGERS:
        raise Forbidden("Only admin can create admin/principal/sub-admin accounts")
    raise Forbidden("Not allowed to import users")


def authorize(db: Db, actor: CurrentUser, user_type: str, class_id: int | None,
              section_id: int | None) -> dict | None:
    """Checks the caller may import this type (and section, for students).
    Returns the section (with class and section names) for students."""
    ensure_can_import_type(actor, user_type)
    if user_type != "student":
        return None
    if class_id is None or section_id is None:
        raise BadRequest("Choose the class and section the students belong to (class_id and section_id).")
    access.ensure_can_edit_students(db, actor, class_id, section_id)
    section = db.one(
        "SELECT s.id AS section_id, s.class_id, c.name AS class_name, s.name AS section_name "
        "FROM sections s JOIN classes c ON c.id = s.class_id WHERE s.id = %s AND s.class_id = %s AND s.org_id = %s",
        (section_id, class_id, db.org),
    )
    if not section:
        raise NotFound("Section not found in that class")
    return section


# ------------------------------------------------------------- endpoints

def describe(actor: CurrentUser, user_type: str) -> dict:
    ensure_can_import_type(actor, user_type)
    return {
        "user_type": user_type,
        "needs_section": user_type == "student",
        "match_on": "username",
        "max_rows": settings.import_max_rows,
        "max_csv_mb": settings.import_max_csv_mb,
        "max_zip_mb": settings.import_max_zip_mb,
        "photo_max_mb": PHOTO_MAX_MB,
        "columns": [c.describe() for c in columns_for(user_type)],
    }


def template(actor: CurrentUser, user_type: str) -> tuple[bytes, str]:
    ensure_can_import_type(actor, user_type)
    return csv_io.write([c.name for c in columns_for(user_type)], []), f"{user_type}_import_template.csv"


def export(db: Db, actor: CurrentUser, user_type: str, class_id: int | None,
           section_id: int | None) -> tuple[bytes, str]:
    """Current users in the import format (password and photo left empty),
    to edit in a spreadsheet and upload again."""
    section = authorize(db, actor, user_type, class_id, section_id)
    sql = planner.EXISTING_SQL + "WHERE u.org_id = %s AND u.user_type = %s"
    params: list = [db.org, user_type]
    if section:
        sql += " AND sp.section_id = %s ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name, u.id"
        params.append(section["section_id"])
    else:
        sql += " ORDER BY u.full_name, u.id"
    users = db.all(sql, params)

    columns = columns_for(user_type)
    table = "student_profiles" if user_type == "student" else "teacher_profiles"
    rows = []
    for user in users:
        row = planner.RowPlan(number=0, username=user["username"], existing=user)
        rows.append([
            "" if c.table is None else _export_value(row.final(c.name, table))
            for c in columns
        ])
    audit.record(db, actor, "users.export", "user", None,
                 {"user_type": user_type, "section_id": section and section["section_id"], "count": len(users)})
    where = f"_{section['class_name']}-{section['section_name']}" if section else ""
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{user_type}s{where}_{school_today().isoformat()}.csv")
    return csv_io.write([c.name for c in columns], rows), filename


def _export_value(value: object) -> object:
    return value.isoformat() if hasattr(value, "isoformat") else value


def run_import(db: Db, actor: CurrentUser, *, user_type: str, class_id: int | None, section_id: int | None,
               csv_file: UploadFile, photos_file: UploadFile | None, dry_run: bool) -> dict:
    section = authorize(db, actor, user_type, class_id, section_id)
    parsed = csv_io.parse(csv_io.read_upload(csv_file, settings.import_max_csv_mb), user_type,
                          settings.import_max_rows)
    archive = PhotoArchive(photos_file, settings.import_max_zip_mb) if _given(photos_file) else None

    plan = planner.build(db, user_type, section, parsed, archive, lock=False)
    if dry_run:
        return _report(plan, dry_run=True, saved=False)
    if plan.has_errors and not plan.savable():
        failing = plan.summary()["error"]
        raise Unprocessable(f"Nothing was saved: {failing} row{'s have' if failing > 1 else ' has'} problems "
                            "and no other row has anything to save.",
                            extra=_report(plan, dry_run=False, saved=False))

    # slow work before the transaction, so row locks are held only briefly
    hashes = writer.hash_passwords(plan)
    names = writer.photo_names(plan)
    stored = archive.store_all(names) if archive and names else {}
    committed = False
    try:
        with db.transaction():
            locked = planner.build(db, user_type, section, parsed, archive, lock=True, photos_checked=True)
            if locked.signature() != plan.signature():
                raise Conflict(CHANGED_WHILE_SAVING)
            locked.warnings = plan.warnings
            writer.apply(db, locked, hashes, stored)
            _audit(db, actor, locked)
        committed = True
    except DuplicateKey as exc:
        raise Conflict(f"{exc.detail}. Nothing was saved; upload the file again to see the current state.") from exc
    except pymysql.err.OperationalError as exc:
        if exc.args and exc.args[0] in (1205, 1213):  # lock wait timeout / deadlock
            raise Conflict("Another change to the same users is in progress. Nothing was saved; "
                           "try again in a moment.") from exc
        raise
    finally:
        if not committed:
            photos.remove(list(stored.values()))
    return _report(locked, dry_run=False, saved=True)


def _given(upload: UploadFile | None) -> bool:
    return upload is not None and bool(upload.filename)


def _audit(db: Db, actor: CurrentUser, plan: planner.Plan) -> None:
    created = [r.user_id for r in plan.rows_with(planner.CREATE)]
    updated = [r.user_id for r in plan.rows_with(planner.UPDATE)]
    audit.record(db, actor, "users.import", "user", None, {
        "user_type": plan.user_type,
        "section_id": plan.section and plan.section["section_id"],
        "created": created,
        "updated": updated,
        "unchanged": len(plan.rows_with(planner.UNCHANGED)),
        "skipped_rows": [r.number for r in plan.rows if r.errors],
    })


def _report(plan: planner.Plan, *, dry_run: bool, saved: bool) -> dict:
    section = plan.section or {}
    return {
        "user_type": plan.user_type,
        "class_id": section.get("class_id"),
        "section_id": section.get("section_id"),
        "class_name": section.get("class_name"),
        "section_name": section.get("section_name"),
        "dry_run": dry_run,
        "saved": saved,
        "summary": plan.summary(),
        "columns": [c.name for c in plan.columns],
        "warnings": plan.warnings,
        "rows": [r.report() for r in plan.rows],
    }
