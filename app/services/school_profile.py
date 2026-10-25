"""The school's own profile, shown in the apps (School Info screen, home
header): name, logo, director, principal, affiliation, contacts, address and
the current academic session.

The name and login code belong to the platform operator (owner portal); the
school edits everything else (admin / principal).
"""
from __future__ import annotations

from datetime import date

from fastapi import UploadFile

from app.core.clock import school_today
from app.core.errors import NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.school import SchoolProfileUpdate
from app.services import audit, storage

PROFILE_FIELDS = ("director_name", "principal_name", "affiliation_no", "phone", "tel", "email",
                  "website", "address", "session_start_month")
_SELECT = "SELECT id, code, name, logo_path, " + ", ".join(PROFILE_FIELDS) + " FROM organisations WHERE id = %s"


def session_bounds(start_month: int, today: date | None = None) -> tuple[date, date]:
    """First and last day of the academic session that contains `today`."""
    today = today or school_today()
    start_month = start_month if 1 <= (start_month or 0) <= 12 else 4
    year = today.year if today.month >= start_month else today.year - 1
    start = date(year, start_month, 1)
    end_year, end_month = (year + 1, start_month - 1) if start_month > 1 else (year, 12)
    next_first = date(end_year + (end_month // 12), end_month % 12 + 1, 1)
    return start, date.fromordinal(next_first.toordinal() - 1)


def session_label(start: date, end: date) -> str:
    return str(start.year) if start.year == end.year else f"{start.year}-{end.year}"


def current_session(db: Db, org_id: int | None = None) -> dict:
    month = db.scalar("SELECT session_start_month FROM organisations WHERE id = %s",
                      (org_id if org_id is not None else db.org,))
    start, end = session_bounds(int(month or 4))
    return {"label": session_label(start, end), "start": start, "end": end}


def get_profile(db: Db, org_id: int | None = None) -> dict:
    org_id = org_id if org_id is not None else db.org
    row = db.one(_SELECT, (org_id,))
    if row is None:
        raise NotFound("School not found")
    if not row["principal_name"]:  # fall back to the principal's account
        row["principal_name"] = db.scalar(
            "SELECT full_name FROM users WHERE org_id = %s AND user_type = 'principal' AND is_active = 1 "
            "ORDER BY id LIMIT 1", (org_id,))
    start, end = session_bounds(int(row["session_start_month"] or 4))
    row["session"] = {"label": session_label(start, end), "start": start, "end": end}
    return row


def update_profile(db: Db, actor: CurrentUser | None, body: SchoolProfileUpdate, org_id: int | None = None,
                   operator: str | None = None) -> dict:
    org_id = org_id if org_id is not None else db.org
    changes = body.model_dump(exclude_unset=True)
    if changes.get("session_start_month", 0) is None:
        changes.pop("session_start_month")  # NOT NULL column: null means "keep"
    if changes:
        with db.transaction():
            if not db.one("SELECT id FROM organisations WHERE id = %s FOR UPDATE", (org_id,)):
                raise NotFound("School not found")
            assignments = ", ".join(f"{k} = %s" for k in changes)
            db.execute(f"UPDATE organisations SET {assignments} WHERE id = %s", (*changes.values(), org_id))
            details = {"fields": sorted(changes)}
            if operator:
                details["operator"] = operator
            audit.record(db, actor, "school.profile_update", "organisation", org_id, details)
    return get_profile(db, org_id)


def upload_logo(db: Db, actor: CurrentUser, file: UploadFile) -> dict:
    with db.transaction():
        current = db.one("SELECT logo_path FROM organisations WHERE id = %s FOR UPDATE", (db.org,))
        stored = storage.save_upload(db, file, "logos", storage.IMAGE_TYPES, max_mb=5,
                                     type_error="The logo must be a JPEG, PNG or WebP image")
        db.execute("UPDATE organisations SET logo_path = %s WHERE id = %s", (stored.path, db.org))
        storage.delete_after_commit(db, current["logo_path"] if current else None)
        audit.record(db, actor, "school.logo_upload", "organisation", db.org)
    return {"logo_path": stored.path}


def delete_logo(db: Db, actor: CurrentUser) -> None:
    with db.transaction():
        current = db.one("SELECT logo_path FROM organisations WHERE id = %s FOR UPDATE", (db.org,))
        if current and current["logo_path"]:
            db.execute("UPDATE organisations SET logo_path = NULL WHERE id = %s", (db.org,))
            storage.delete_after_commit(db, current["logo_path"])
            audit.record(db, actor, "school.logo_delete", "organisation", db.org)
