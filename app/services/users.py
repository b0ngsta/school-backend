"""Staff accounts, profile photos and teacher details."""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import BadRequest, Conflict, DuplicateKey, Forbidden, NotFound
from app.core.roles import ADMIN_MANAGED, MANAGERS
from app.core.security import hash_password
from app.db import Db
from app.models.auth import CurrentUser
from app.models.users import StaffCreate, StaffUpdate, TeacherInfoUpdate
from app.services import access, audit, pagination, storage

ONLY_ADMIN_CREATES = "Only admin can create admin/principal/sub-admin accounts"


def ensure_no_other_principal(db: Db, user_id: int | None = None) -> None:
    """There is only one principal. (A UNIQUE index enforces the same rule in
    the database, for requests that race past this check.)"""
    row = db.one("SELECT id, username FROM users WHERE org_id = %s AND user_type = 'principal' LIMIT 1", (db.org,))
    if row and row["id"] != user_id:
        raise Conflict(f"There is already a principal ({row['username']}). Only one principal is allowed.")


def create_staff(db: Db, actor: CurrentUser, body: StaffCreate) -> dict:
    # only admin can create admin/principal/sub_admin accounts
    if body.user_type in ADMIN_MANAGED and actor.user_type != "admin":
        raise Forbidden(ONLY_ADMIN_CREATES)
    with db.transaction():
        if body.user_type == "principal":
            ensure_no_other_principal(db)
        if db.one("SELECT id FROM users WHERE org_id = %s AND username = %s", (db.org, body.username)):
            raise Conflict("Username already exists")
        try:
            user_id = db.execute(
                "INSERT INTO users (org_id, username, password_hash, full_name, email, phone, user_type) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (db.org, body.username, hash_password(body.password), body.full_name,
                 body.email, body.phone, body.user_type),
            ).lastrowid
        except DuplicateKey as exc:
            if exc.key in (None, "uq_users_org_username"):
                raise Conflict("Username already exists") from None
            raise
        if body.user_type == "teacher":
            info = body.teacher_info or TeacherInfoUpdate()
            db.execute(
                "INSERT INTO teacher_profiles (user_id, designation, subject, qualification, joining_date, address) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, info.designation, info.subject, info.qualification, info.joining_date, info.address),
            )
        audit.record(db, actor, "user.create", "user", user_id,
                     {"username": body.username, "user_type": body.user_type})
    return {"id": user_id, "username": body.username, "user_type": body.user_type}


def update_staff(db: Db, actor: CurrentUser, user_id: int, body: StaffUpdate) -> dict:
    """Name, email and phone of a staff account. Fields left out keep their
    value. Editing mirrors creating: admin/principal/sub-admin accounts are
    admin-only; students are edited through /students."""
    changes = body.model_dump(exclude_unset=True)
    with db.transaction():
        target = db.one("SELECT id, user_type, full_name, email, phone FROM users "
                        "WHERE id = %s AND org_id = %s FOR UPDATE", (user_id, db.org))
        if not target:
            raise NotFound("User not found")
        if target["user_type"] == "student":
            raise BadRequest("Students are edited with PUT /students/{id}")
        if target["user_type"] in ADMIN_MANAGED and actor.user_type != "admin":
            raise Forbidden("Only admin can edit this account")
        if body.full_name is None:
            changes.pop("full_name", None)  # the name can be changed, not removed
        merged = {**target, **changes}
        db.execute("UPDATE users SET full_name = %s, email = %s, phone = %s WHERE id = %s",
                   (merged["full_name"], merged["email"], merged["phone"], user_id))
        audit.record(db, actor, "user.update", "user", user_id, {"fields": sorted(changes)})
    return {"updated": user_id}


def list_users(db: Db, user_type: str | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql = (
        "SELECT id, username, full_name, email, phone, photo_path, user_type, is_active, created_at "
        "FROM users WHERE org_id = %s"
    )
    params: tuple = (db.org,)
    if user_type:
        sql += " AND user_type = %s"
        params = (db.org, user_type)
    else:
        sql += " AND user_type != 'student'"
    return pagination.fetch(db, sql, params, "user_type, full_name, id", page)


def delete_user(db: Db, actor: CurrentUser, user_id: int) -> dict:
    if user_id == actor.id:
        raise BadRequest("Cannot delete yourself")
    with db.transaction():
        target = db.one("SELECT user_type, username, photo_path FROM users WHERE id = %s AND org_id = %s FOR UPDATE",
                        (user_id, db.org))
        if not target:
            raise NotFound("User not found")
        # deleting mirrors creating: admin/principal/sub_admin accounts are admin-only
        if target["user_type"] in ADMIN_MANAGED and actor.user_type != "admin":
            raise Forbidden("Only admin can delete this account")
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))
        storage.delete_after_commit(db, target["photo_path"])
        audit.record(db, actor, "user.delete", "user", user_id,
                     {"username": target["username"], "user_type": target["user_type"]})
    return {"deleted": user_id}


def upload_photo(db: Db, actor: CurrentUser, user_id: int, file: UploadFile) -> dict:
    """Staff photos: managers. Student photos: managers or the class teacher."""
    with db.transaction():
        target = db.one("SELECT id, user_type, photo_path FROM users WHERE id = %s AND org_id = %s FOR UPDATE",
                        (user_id, db.org))
        if not target:
            raise NotFound("User not found")
        if target["user_type"] == "student":
            sp = db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s", (user_id,)) \
                or {"class_id": None, "section_id": None}
            access.ensure_can_edit_students(db, actor, sp["class_id"], sp["section_id"])
        elif actor.user_type not in MANAGERS:
            raise Forbidden("Not allowed")
        stored = storage.save_upload(db, file, "photos", storage.IMAGE_TYPES, max_mb=5)
        db.execute("UPDATE users SET photo_path = %s WHERE id = %s", (stored.path, user_id))
        storage.delete_after_commit(db, target["photo_path"])
    return {"photo_path": stored.path}


def list_teachers(db: Db) -> list[dict]:
    return db.all(
        "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.photo_path, u.is_active, "
        "t.designation, t.subject, t.qualification, t.joining_date, t.address, "
        "(SELECT COUNT(*) FROM lesson_plans lp WHERE lp.teacher_id = u.id) AS lesson_plan_count "
        "FROM users u LEFT JOIN teacher_profiles t ON t.user_id = u.id "
        "WHERE u.org_id = %s AND u.user_type = 'teacher' ORDER BY u.full_name",
        (db.org,),
    )


def update_teacher_info(db: Db, user_id: int, body: TeacherInfoUpdate) -> dict:
    """Fields left out of the request keep their current value."""
    with db.transaction():
        if not db.one("SELECT id FROM users WHERE id = %s AND org_id = %s AND user_type = 'teacher'",
                      (user_id, db.org)):
            raise NotFound("Teacher not found")
        current = db.one(
            "SELECT designation, subject, qualification, joining_date, address FROM teacher_profiles "
            "WHERE user_id = %s FOR UPDATE",
            (user_id,),
        ) or {}
        merged = {**{k: current.get(k) for k in ("designation", "subject", "qualification", "joining_date",
                                                  "address")},
                  **body.model_dump(exclude_unset=True)}
        db.execute(
            "INSERT INTO teacher_profiles (user_id, designation, subject, qualification, joining_date, address) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE designation = VALUES(designation), subject = VALUES(subject), "
            "qualification = VALUES(qualification), joining_date = VALUES(joining_date), address = VALUES(address)",
            (user_id, merged["designation"], merged["subject"], merged["qualification"], merged["joining_date"],
             merged["address"]),
        )
    return {"updated": user_id}
