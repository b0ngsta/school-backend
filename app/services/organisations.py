"""Organisations (schools). One API serves many schools; every user and
every school record belongs to exactly one organisation.

* Login, forgot-password and reset name the school by its code
  (`org_code`); without one, DEFAULT_ORG_CODE is used (apps released before
  school codes keep working). Usernames are unique per school, so two
  schools can both have an "admin".
* After authentication the request's Db is bound to the caller's
  organisation and every service query filters on it (see db/session.py).
* New schools are added by the platform operator: `python -m app.cli
  create-org` or POST /platform/organisations (X-Platform-Secret).
"""
from __future__ import annotations

import re

from app.core.config import settings
from app.core.errors import BadRequest, Conflict, DuplicateKey, Forbidden, NotFound
from app.core.security import hash_password
from app.db import Db
from app.models.organisations import OrganisationCreate, OrganisationUpdate
from app.services import audit

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,29}$")
INVALID_CODE = "School codes are 2 to 30 letters, digits or hyphens"
UNKNOWN_CODE = "School code not found. Check the code with your school office."
DISABLED = "This school's account is disabled. Contact the school office."
CODE_REQUIRED = "Enter your school code"

# every new school starts with the same calendar event names as the original one
DEFAULT_EVENTS = (("Holiday", "holiday"), ("Exam", "exam"), ("PTM", "event"), ("Annual Function Day", "event"))

_COLUMNS = "id, code, name, is_active, created_at, updated_at"


def normalize_code(code: str | None) -> str:
    value = (code or "").strip().upper()
    if not _CODE_RE.match(value):
        raise BadRequest(INVALID_CODE)
    return value


def find(db: Db, code: str) -> dict | None:
    return db.one(f"SELECT {_COLUMNS} FROM organisations WHERE code = %s", (code.strip().upper(),))


def get(db: Db, org_id: int) -> dict | None:
    return db.one(f"SELECT {_COLUMNS} FROM organisations WHERE id = %s", (org_id,))


def resolve(db: Db, code: str | None) -> dict:
    """The active organisation a login / password-reset request is for, and
    bind the request to it."""
    raw = (code or "").strip() or settings.default_org_code
    if not raw:
        raise BadRequest(CODE_REQUIRED)
    org = find(db, raw)
    if org is None:
        raise BadRequest(UNKNOWN_CODE)
    if not org["is_active"]:
        raise Forbidden(DISABLED)
    db.bind_org(org["id"])
    return org


def public_info(db: Db, code: str) -> dict:
    """What a login screen may show before sign-in: the school's name."""
    org = find(db, code) if _CODE_RE.match((code or "").strip().upper()) else None
    if org is None or not org["is_active"]:
        raise NotFound("School not found")
    return {"code": org["code"], "name": org["name"]}


# ---------------------------------------------------- platform operator

def list_all(db: Db) -> list[dict]:
    return db.all(
        f"SELECT o.{_COLUMNS.replace(', ', ', o.')}, "
        "(SELECT COUNT(*) FROM users u WHERE u.org_id = o.id AND u.user_type = 'student') AS students, "
        "(SELECT COUNT(*) FROM users u WHERE u.org_id = o.id AND u.user_type != 'student') AS staff "
        "FROM organisations o ORDER BY o.id"
    )


def create(db: Db, body: OrganisationCreate, operator: str | None = None) -> dict:
    """A new school with its first admin account (who must change the
    password at first login) and the default calendar event names.
    `operator` (who did it, from the owner portal) goes into the audit log."""
    code = normalize_code(body.code)
    with db.transaction():
        if find(db, code):
            raise Conflict(f"The school code {code} is already taken")
        try:
            org_id = db.execute("INSERT INTO organisations (code, name) VALUES (%s, %s)",
                                (code, body.name.strip())).lastrowid
        except DuplicateKey:
            raise Conflict(f"The school code {code} is already taken") from None
        db.bind_org(org_id)
        admin_id = db.execute(
            "INSERT INTO users (org_id, username, password_hash, full_name, email, user_type, must_change_password) "
            "VALUES (%s, %s, %s, %s, %s, 'admin', 1)",
            (org_id, body.admin_username.strip(), hash_password(body.admin_password), body.admin_full_name.strip(),
             body.admin_email or None),
        ).lastrowid
        db.execute_many("INSERT INTO calendar_events (org_id, title, category) VALUES (%s, %s, %s)",
                        [(org_id, title, category) for title, category in DEFAULT_EVENTS])
        audit.record(db, None, "organisation.create", "organisation", org_id,
                     {"code": code, "name": body.name, "admin_username": body.admin_username,
                      **({"operator": operator} if operator else {})})
    return {"id": org_id, "code": code, "name": body.name.strip(),
            "admin": {"id": admin_id, "username": body.admin_username.strip()}}


def update(db: Db, code: str, body: OrganisationUpdate, operator: str | None = None) -> dict:
    """Rename a school, change its login code, or switch it off/on. A
    disabled school's users are refused at their next request (their tokens
    stop working at once). Signed-in users stay signed in after a code change;
    they type the new code at their next login. If the school is the one
    DEFAULT_ORG_CODE points at, change that setting too."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "code" in changes:
        changes["code"] = normalize_code(changes["code"])
    with db.transaction():
        org = db.one(f"SELECT {_COLUMNS} FROM organisations WHERE code = %s FOR UPDATE",
                     (normalize_code(code),))
        if org is None:
            raise NotFound("School not found")
        merged = {**org, **changes}
        if merged["code"] != org["code"] and find(db, merged["code"]):
            raise Conflict(f"The school code {merged['code']} is already taken")
        try:
            db.execute("UPDATE organisations SET code = %s, name = %s, is_active = %s WHERE id = %s",
                       (merged["code"], merged["name"].strip(), int(bool(merged["is_active"])), org["id"]))
        except DuplicateKey:
            raise Conflict(f"The school code {merged['code']} is already taken") from None
        db.bind_org(org["id"])
        audit.record(db, None, "organisation.update", "organisation", org["id"],
                     {**changes, **({"operator": operator} if operator else {})})
    return get(db, org["id"])
