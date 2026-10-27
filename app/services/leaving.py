"""People leaving the school, and coming back.

Accounts are never deleted: `DELETE /users/{id}` and `DELETE /students/{id}`
mark the account as left (migration 0013). Everything the person did or that
is about them stays: attendance they marked, remarks and report cards they
wrote, a student's fees, payments, marks and attendance.

Marking someone as left:
* signs them out everywhere at once (token_version + 1, sessions revoked,
  push devices forgotten) and blocks sign-in (is_active = 0);
* takes them off what is *current*: a student leaves their class list (their
  class / section move to last_class_id / last_section_id, which frees the
  roll number) and their bus; a teacher leaves their class / subject
  assignments and timetable periods; a driver leaves their vehicle.

Reactivating puts a student back in their last section when it still exists
(without the roll number if someone else has taken it). Staff get their
account back; assignments and timetable periods are set up again by the office.
"""
from __future__ import annotations

from app.core.clock import school_today
from app.core.errors import BadRequest, Conflict, Forbidden, NotFound
from app.core.roles import ADMIN_MANAGED
from app.db import Db
from app.models.auth import CurrentUser
from app.services import audit, notifications, sessions

ONLY_ADMIN = "Only admin can change this account"
USERNAME_TAKEN = "Username already exists"


def username_taken(db: Db, username: str) -> str | None:
    """The Conflict message when `username` is in use in this school, else None.
    A left account keeps its username (so it can be reactivated): say so."""
    row = db.one("SELECT full_name, left_on FROM users WHERE org_id = %s AND username = %s", (db.org, username))
    if row is None:
        return None
    if row["left_on"] is not None:
        return (f"{USERNAME_TAKEN}: it belongs to {row['full_name']}, who is marked as left. "
                "Reactivate that account, or choose another username.")
    return USERNAME_TAKEN


def _target(db: Db, user_id: int) -> dict:
    row = db.one(
        "SELECT id, username, full_name, user_type, is_active, left_on FROM users "
        "WHERE id = %s AND org_id = %s FOR UPDATE",
        (user_id, db.org),
    )
    if not row:
        raise NotFound("User not found")
    return row


def _check_rights(actor: CurrentUser, target: dict) -> None:
    if target["id"] == actor.id:
        raise BadRequest("You can't mark your own account as left")
    # mirrors creating: admin / principal / sub-admin accounts are admin-only
    if target["user_type"] in ADMIN_MANAGED and actor.user_type != "admin":
        raise Forbidden(ONLY_ADMIN)


def mark_left(db: Db, actor: CurrentUser, user_id: int, reason: str | None = None) -> dict:
    """Close an account but keep every record (see the module docstring)."""
    reason = (reason or "").strip()[:255] or None
    with db.transaction():
        target = _target(db, user_id)
        _check_rights(actor, target)
        if target["left_on"] is not None:
            raise Conflict("This account is already marked as left")
        today = school_today()
        db.execute(
            "UPDATE users SET is_active = 0, left_on = %s, left_reason = %s, "
            "token_version = token_version + 1 WHERE id = %s",
            (today, reason, user_id),
        )
        sessions.revoke_all(db, user_id, "left")
        notifications.forget_user_devices(db, user_id)
        db.execute("DELETE FROM password_reset_codes WHERE user_id = %s", (user_id,))

        details: dict = {"username": target["username"], "user_type": target["user_type"], "reason": reason}
        if target["user_type"] == "student":
            profile = db.one("SELECT class_id, section_id, roll_no FROM student_profiles WHERE user_id = %s",
                             (user_id,))
            if profile:
                details.update(profile)
                db.execute(
                    "UPDATE student_profiles SET last_class_id = class_id, last_section_id = section_id, "
                    "class_id = NULL, section_id = NULL WHERE user_id = %s",
                    (user_id,),
                )
            db.execute("DELETE FROM transport_shift_students WHERE student_id = %s", (user_id,))
            db.execute("DELETE FROM student_transport WHERE student_id = %s AND org_id = %s", (user_id, db.org))
            action = "student.left"
        else:
            details["assignments_removed"] = db.execute(
                "DELETE FROM teacher_assignments WHERE teacher_id = %s AND org_id = %s", (user_id, db.org)
            ).rowcount
            details["periods_removed"] = db.execute(
                "DELETE FROM timetable_slots WHERE teacher_id = %s AND org_id = %s", (user_id, db.org)
            ).rowcount
            db.execute("UPDATE vehicles SET driver_id = NULL WHERE driver_id = %s AND org_id = %s",
                       (user_id, db.org))
            details["substitutions_closed"] = _close_open_substitutions(db, user_id, today)
            details["leaves_cancelled"] = db.execute(
                "UPDATE leave_requests SET status = 'cancelled' WHERE org_id = %s AND user_id = %s "
                "AND status = 'pending'", (db.org, user_id)
            ).rowcount
            action = "user.left"
        audit.record(db, actor, action, "user", user_id, details)
    # "deleted" kept for clients written before accounts were kept
    return {"deleted": user_id, "left": user_id, "left_on": str(today)}


def _close_open_substitutions(db: Db, user_id: int, today) -> int:
    """Someone who left can't cover a period any more, and nobody needs to cover
    theirs: close what is still open from today on (history stays as it was)."""
    note = "Left the school"
    closed = db.execute(
        "UPDATE substitution_requests SET status = 'declined', responded_at = NOW(), response_note = %s "
        "WHERE org_id = %s AND substitute_id = %s AND status = 'pending' AND date >= %s",
        (note, db.org, user_id, today),
    ).rowcount
    closed += db.execute(
        "UPDATE substitution_requests SET status = 'withdrawn', responded_at = NOW(), response_note = %s "
        "WHERE org_id = %s AND substitute_id = %s AND status = 'accepted' AND date >= %s",
        (note, db.org, user_id, today),
    ).rowcount
    closed += db.execute(
        "UPDATE substitution_requests SET status = 'cancelled' "
        "WHERE org_id = %s AND requester_id = %s AND status IN ('pending', 'accepted') AND date >= %s",
        (db.org, user_id, today),
    ).rowcount
    return closed


def reactivate(db: Db, actor: CurrentUser, user_id: int) -> dict:
    """Undo `mark_left` (or switch a disabled account back on)."""
    result: dict = {"reactivated": user_id}
    with db.transaction():
        target = _target(db, user_id)
        _check_rights(actor, target)
        if target["is_active"] and target["left_on"] is None:
            raise Conflict("This account is already active")
        if target["user_type"] == "principal" and db.one(
            "SELECT id FROM users WHERE org_id = %s AND user_type = 'principal' AND left_on IS NULL "
            "AND id <> %s LIMIT 1", (db.org, user_id)
        ):
            raise Conflict("The school already has a principal. Only one principal is allowed.")
        db.execute("UPDATE users SET is_active = 1, left_on = NULL, left_reason = NULL WHERE id = %s", (user_id,))

        if target["user_type"] == "student":
            profile = db.one(
                "SELECT sp.class_id, sp.section_id, sp.last_class_id, sp.last_section_id, sp.roll_no, "
                "s.id AS section_ok FROM student_profiles sp "
                "LEFT JOIN sections s ON s.id = sp.last_section_id AND s.class_id = sp.last_class_id "
                "AND s.org_id = %s WHERE sp.user_id = %s FOR UPDATE",
                (db.org, user_id),
            )
            if profile and profile["class_id"] is None and profile["section_ok"]:
                taken = profile["roll_no"] and db.one(
                    "SELECT user_id FROM student_profiles WHERE section_id = %s AND roll_no = %s",
                    (profile["last_section_id"], profile["roll_no"]),
                )
                db.execute(
                    "UPDATE student_profiles SET class_id = last_class_id, section_id = last_section_id, "
                    "last_class_id = NULL, last_section_id = NULL"
                    + (", roll_no = NULL" if taken else "") + " WHERE user_id = %s",
                    (user_id,),
                )
                result.update(class_id=profile["last_class_id"], section_id=profile["last_section_id"],
                              roll_no_cleared=bool(taken))
            elif profile and profile["class_id"] is None:
                result["needs_class"] = True  # the old section is gone: put the student in a class
        audit.record(db, actor, "user.reactivate", "user", user_id, {"username": target["username"]})
    return result
