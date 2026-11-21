"""Students' logins for the class teacher to hand to parents (owner's choice "B", 21 Nov 2026).

Passwords are stored one-way, so nobody can look one up later. Instead the
class teacher (or the office) taps "New password & share": the student gets a
new easy password (services/passwords.py), shown ONCE in the answer together
with a ready-to-send message (WhatsApp etc.); the parent must choose their own
password at the first sign-in. Nothing readable is stored.

    GET  /students/login-status?section_id=   the section's students: username, signed in?, last shared
    POST /students/{id}/login-share           new password + the message to send (shown once)

Who: the section's CLASS teacher, or MANAGERS (like adding / editing students).
A new password signs the child's account out everywhere (a parent already
using the app signs in again with it): `was_signed_in` says so. Brothers and
sisters share one username but each has their own password; the parent can
switch between them in the app once signed in to one.
"""
from __future__ import annotations

from app.core.errors import BadRequest, Conflict, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.services import access, audit, labels, passwords, school_profile
from app.services import auth as auth_service

APP_NAME = "Escolva"


def _utc_iso(value) -> str | None:
    return None if value is None else value.isoformat(timespec="seconds") + "+00:00"


def section_status(db: Db, actor: CurrentUser, section_id: int) -> dict:
    section = db.one(
        "SELECT s.id, s.class_id, s.name, c.name AS class_name FROM sections s JOIN classes c ON c.id = s.class_id "
        "WHERE s.id = %s AND s.org_id = %s", (section_id, db.org))
    if not section:
        raise NotFound("Section not found")
    access.ensure_can_edit_students(db, actor, section["class_id"], section["id"])
    rows = db.all(
        "SELECT u.id, u.full_name, u.username, u.photo_path, u.must_change_password, sp.roll_no, sp.admission_no, "
        "sp.father_name, sp.guardian_phone, "
        "(SELECT MAX(a.last_used_at) FROM auth_sessions a WHERE a.user_id = u.id AND a.revoked_at IS NULL "
        " AND a.expires_at > UTC_TIMESTAMP()) AS last_seen_at, "
        "(SELECT COUNT(*) FROM users o WHERE o.org_id = u.org_id AND o.username = u.username AND o.id <> u.id "
        " AND o.user_type = 'student' AND o.left_on IS NULL) AS siblings "
        "FROM users u JOIN student_profiles sp ON sp.user_id = u.id "
        "WHERE u.org_id = %s AND sp.section_id = %s AND u.user_type = 'student' AND u.left_on IS NULL "
        "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name, u.id", (db.org, section_id))
    shared: dict[str, object] = {}
    if rows:
        ids = [str(r["id"]) for r in rows]
        shared = {r["entity_id"]: r["at"] for r in db.all(
            "SELECT entity_id, MAX(created_at) AS at FROM audit_logs WHERE org_id = %s AND entity_type = 'user' "
            f"AND action = 'student.login_shared' AND entity_id IN ({', '.join(['%s'] * len(ids))}) "
            "GROUP BY entity_id", (db.org, *ids))}
    return {
        "section_id": section["id"], "class_id": section["class_id"],
        "class_label": labels.class_section(section["class_name"], section["name"] or None),
        "students": [{
            "id": r["id"], "full_name": r["full_name"], "username": r["username"], "photo_path": r["photo_path"],
            "roll_no": r["roll_no"], "admission_no": r["admission_no"], "father_name": r["father_name"],
            "guardian_phone": r["guardian_phone"],
            "signed_in": r["last_seen_at"] is not None, "last_seen_at": _utc_iso(r["last_seen_at"]),
            "must_change_password": bool(r["must_change_password"]),
            "last_shared_at": shared[str(r["id"])].isoformat(timespec="seconds") if str(r["id"]) in shared else None,
            "siblings": int(r["siblings"] or 0),
        } for r in rows],
    }


def share(db: Db, actor: CurrentUser, student_id: int) -> dict:
    """A new easy password for the student, returned once with the message to send."""
    with db.transaction():
        student = db.one(
            "SELECT u.id, u.full_name, u.username, u.left_on, sp.class_id, sp.section_id, "
            "c.name AS class_name, s.name AS section_name FROM users u "
            "JOIN student_profiles sp ON sp.user_id = u.id LEFT JOIN classes c ON c.id = sp.class_id "
            "LEFT JOIN sections s ON s.id = sp.section_id "
            "WHERE u.id = %s AND u.org_id = %s AND u.user_type = 'student' FOR UPDATE", (student_id, db.org))
        if not student:
            raise NotFound("Student not found")
        if student["left_on"] is not None:
            raise Conflict(f"{student['full_name']} has left the school. Reactivate them first.",
                           extra={"code": "student_left"})
        if student["section_id"] is None:
            raise BadRequest(f"{student['full_name']} is not in a section yet.")
        access.ensure_can_edit_students(db, actor, student["class_id"], student["section_id"])
        was_signed_in = bool(db.scalar(
            "SELECT COUNT(*) FROM auth_sessions WHERE user_id = %s AND revoked_at IS NULL "
            "AND expires_at > UTC_TIMESTAMP()", (student_id,)))
        password = passwords.easy()
        auth_service.set_password(db, student_id, password, reason="login_shared")  # signs every device out
        db.execute("UPDATE users SET must_change_password = 1 WHERE id = %s", (student_id,))
        audit.record(db, actor, "student.login_shared", "user", student_id,
                     {"username": student["username"], "was_signed_in": was_signed_in})
        org = school_profile.get_profile(db, db.org)
    class_label = labels.class_section(student["class_name"], student["section_name"] or None)
    text = "\n".join([
        f"{APP_NAME} app login for {student['full_name']} ({class_label}), {org['name']}",
        f"School code: {org['code']}",
        f"Username: {student['username']}",
        f"Password: {password}",
        "After signing in you will be asked to choose your own password.",
    ])
    return {
        "student_id": student_id, "full_name": student["full_name"], "class_label": class_label,
        "username": student["username"], "password": password,
        "school_code": org["code"], "school_name": org["name"],
        "was_signed_in": was_signed_in, "share_text": text,
    }
