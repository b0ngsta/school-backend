"""Brothers and sisters: student accounts of one school that share a username.

A parent signs in to the app as their child (school code + username +
password). The office gives the children of one family the same username
(usually a parent's mobile number) and each child their own password; the
password decides which child a sign-in opens (services/auth.py), and the
app's "Switch child" screen moves to another child without asking for a
password again.

The rule (migration 0024):
* only STUDENT accounts may share a username, and only inside one school;
* a staff / driver username is unique in its school, against students too
  (the database enforces staff against staff; this module checks the rest);
* sharing is never an accident: creating or renaming a student to a username
  another student has answers 409 `code: "username_shared"` with those
  students, and goes through only when the request says `sibling: true`.

"Siblings" of a student = the other ACTIVE students of the same school with
the same username. The same username in another school means nothing.
"""
from __future__ import annotations

from app.core.errors import BadRequest, Conflict, NotFound
from app.db import Db
from app.models.auth import CurrentUser, TokenResponse
from app.models.students import StudentUsername
from app.services import audit, leaving, notifications, sessions
from app.services import auth as auth_service

SHARED = "username_shared"
MAX_LISTED = 20

_HOLDERS_SQL = (
    "SELECT u.id, u.full_name, u.user_type, u.left_on, c.name AS class_name, s.name AS section_name "
    "FROM users u "
    "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
    "LEFT JOIN classes c ON c.id = COALESCE(sp.class_id, sp.last_class_id) "
    "LEFT JOIN sections s ON s.id = COALESCE(sp.section_id, sp.last_section_id) "
    "WHERE u.org_id = %s AND u.username = %s ORDER BY u.id LIMIT %s"
)


def lock_school(db: Db) -> None:
    """Serialise account creation / renaming inside one school, so two requests
    at the same moment cannot both pass the username checks. Call inside a transaction."""
    db.one("SELECT id FROM organisations WHERE id = %s FOR UPDATE", (db.org,))


def _public(holder: dict) -> dict:
    return {"id": holder["id"], "full_name": holder["full_name"], "class_name": holder["class_name"],
            "section_name": holder["section_name"], "left": holder["left_on"] is not None}


def _taken(holder: dict) -> str:
    """The wording clients have always had for a username that is in use (leaving.username_taken)."""
    if holder["left_on"] is None:
        return leaving.USERNAME_TAKEN
    return (f"{leaving.USERNAME_TAKEN}: it belongs to {holder['full_name']}, who is marked as left. "
            "Reactivate that account, or choose another username.")


def check_student_username(db: Db, username: str, *, sibling: bool = False,
                           exclude_id: int | None = None) -> list[dict]:
    """May a student account take `username`? Call inside a transaction.

    Free -> []. Held by a staff / driver account -> 409 as always. Held only by
    students -> 409 `username_shared` (the same message as before, plus who
    has it), unless `sibling` confirms that they are brothers and sisters: then
    those students are returned. Callers take `lock_school(db)` as the FIRST statement of their
    transaction: a plain read before the lock would hide a username saved at the same moment."""
    lock_school(db)
    holders = [h for h in db.all(_HOLDERS_SQL, (db.org, username, MAX_LISTED)) if h["id"] != exclude_id]
    if not holders:
        return []
    staff = [h for h in holders if h["user_type"] != "student"]
    if staff:
        raise Conflict(_taken(staff[0]))
    if sibling:
        return [_public(h) for h in holders]
    raise Conflict(_taken(holders[0]), extra={"code": SHARED, "siblings": [_public(h) for h in holders]})


def username_check(db: Db, username: str, student_id: int | None = None) -> dict:
    """GET /students/username-check: what saving `username` for a student would do.
    Read-only (the save itself checks again under the school lock)."""
    value = username.strip()
    if len(value) < 3 or any(ch.isspace() for ch in value):
        raise BadRequest("Username: at least 3 characters, no spaces")
    if student_id is not None:
        me = db.one("SELECT username FROM users WHERE id = %s AND org_id = %s AND user_type = 'student'",
                    (student_id, db.org))
        if not me:
            raise NotFound("Student not found")
        if me["username"] == value:
            return {"username": value, "available": True, "current": True, "taken_by_staff": False, "shared_with": []}
    holders = [h for h in db.all(_HOLDERS_SQL, (db.org, value, MAX_LISTED)) if h["id"] != student_id]
    staff = any(h["user_type"] != "student" for h in holders)
    return {
        "username": value,
        "available": not holders,
        "current": False,
        "taken_by_staff": staff,
        # a staff account's name is not shown: the answer is just "pick another"
        "shared_with": [] if staff else [_public(h) for h in holders],
    }


def rename_student(db: Db, actor: CurrentUser, student_id: int, body: StudentUsername) -> dict:
    """PUT /students/{id}/username: the office gives a student another username,
    e.g. the one a brother or sister already has. The password and the
    student's sign-ins stay as they are."""
    username = body.username.strip()
    with db.transaction():
        lock_school(db)
        target = db.one("SELECT id, username FROM users WHERE id = %s AND org_id = %s AND user_type = 'student' "
                        "FOR UPDATE", (student_id, db.org))
        if not target:
            raise NotFound("Student not found")
        if target["username"] == username:
            return {"updated": student_id, "username": username, "siblings": [], "changed": False}
        shared_with = check_student_username(db, username, sibling=body.sibling, exclude_id=student_id)
        db.execute("UPDATE users SET username = %s WHERE id = %s", (username, student_id))
        audit.record(db, actor, "student.username_change", "user", student_id,
                     {"from": target["username"], "to": username, "siblings": [s["id"] for s in shared_with]})
    return {"updated": student_id, "username": username, "siblings": shared_with, "changed": True}


def list_for(db: Db, user: CurrentUser) -> list[dict]:
    """GET /student/siblings: the caller's brothers and sisters (never the caller)."""
    return db.all(
        "SELECT u.id, u.full_name, c.name AS class_name, s.name AS section_name, sp.roll_no, u.photo_path "
        "FROM users u "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN classes c ON c.id = sp.class_id "
        "LEFT JOIN sections s ON s.id = sp.section_id "
        "WHERE u.org_id = %s AND u.username = %s AND u.user_type = 'student' AND u.is_active = 1 "
        "AND u.left_on IS NULL AND u.id <> %s ORDER BY u.full_name, u.id LIMIT %s",
        (db.org, user.username, user.id, MAX_LISTED),
    )


def switch(db: Db, user: CurrentUser, student_id: int) -> TokenResponse:
    """POST /auth/switch-sibling: sign this phone in as a brother or sister.

    The target must be an active student of the caller's school with the
    caller's username (anything else: 404). The caller's session ends (the
    phone holds one sign-in at a time, like after a normal sign-in) and the
    reply is an ordinary token response for the other child."""
    with db.transaction():
        target = db.one(
            sessions.TOKEN_USER_SQL + " AND u.org_id = %s AND u.username = %s AND u.user_type = 'student' "
            "AND u.is_active = 1 AND u.left_on IS NULL AND u.id <> %s",
            (student_id, db.org, user.username, user.id),
        )
        if not target:
            raise NotFound("Student not found")
        sessions.revoke(db, user.session_id, "switch_sibling")
        session = sessions.open_session(db, target["id"])
        # the phone keeps getting every child's pushes (migration 0026); they now hang on the new sign-in
        notifications.move_session_devices(db, user.session_id, session.session_id)
        audit.record(db, user, "auth.switch_sibling", "user", target["id"], {"from": user.id, "to": target["id"]})
    return auth_service.token_response(target, session, db)
