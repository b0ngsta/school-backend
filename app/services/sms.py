"""Bulk SMS — compose + history.

Sending is SIMULATED: messages are stored with recipient counts but no SMS
actually goes out. Plug a provider (MSG91 / Twilio) into _send_sms().
"""
from __future__ import annotations

from app.core.errors import BadRequest, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.sms import SmsCreate
from app.services import pagination


def _send_sms(numbers: list[str], message: str) -> bool:
    """STUB — integrate MSG91/Twilio here later. Returns success."""
    return True


def _phones(rows: list[dict]) -> list[str]:
    return [r["phone"] for r in rows if r["phone"]]


def _resolve_recipients(db: Db, group: str) -> tuple[str, list[str]]:
    """Returns (human label, phone numbers)."""
    if group == "all_parents":
        return "All Parents", _phones(db.all(
            "SELECT COALESCE(sp.guardian_phone, u.phone) AS phone "
            "FROM users u LEFT JOIN student_profiles sp ON sp.user_id = u.id "
            "WHERE u.org_id = %s AND u.user_type = 'student' AND u.is_active = 1", (db.org,)
        ))
    if group == "all_teachers":
        return "All Teachers", _phones(db.all(
            "SELECT phone FROM users WHERE org_id = %s AND user_type = 'teacher' AND is_active = 1", (db.org,)
        ))
    if group == "all_staff":
        return "All Staff", _phones(db.all(
            "SELECT phone FROM users WHERE org_id = %s AND user_type != 'student' AND is_active = 1", (db.org,)
        ))
    if group.startswith("section:"):
        raw_id = group.split(":", 1)[1].strip()
        if not raw_id.isdigit():
            raise BadRequest("Unknown recipient group")
        section_id = int(raw_id)
        sec = db.one(
            "SELECT c.name AS class_name, s.name AS section_name FROM sections s "
            "JOIN classes c ON c.id = s.class_id WHERE s.id = %s AND s.org_id = %s",
            (section_id, db.org),
        )
        if not sec:
            raise NotFound("Section not found")
        label = f"{sec['class_name']} — {sec['section_name']} Parents"
        return label, _phones(db.all(
            "SELECT COALESCE(sp.guardian_phone, u.phone) AS phone "
            "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
            "WHERE sp.section_id = %s AND u.is_active = 1",
            (section_id,),
        ))
    raise BadRequest("Unknown recipient group")


def history(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    """Latest 100 by default (as before); pages reach the full history."""
    return pagination.fetch(
        db,
        "SELECT m.*, u.full_name AS sent_by FROM sms_messages m LEFT JOIN users u ON u.id = m.created_by "
        "WHERE m.org_id = %s",
        (db.org,),
        "m.created_at DESC, m.id DESC",
        page,
        default_limit=100,
    )


def send(db: Db, actor: CurrentUser, body: SmsCreate) -> dict:
    label, numbers = _resolve_recipients(db, body.recipient_group)
    ok = _send_sms(numbers, body.message)
    status = "sent" if ok else "failed"
    message_id = db.execute(
        "INSERT INTO sms_messages "
        "(org_id, recipient_group, template, message, recipients_count, status, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (db.org, label, body.template, body.message, len(numbers), status, actor.id),
    ).lastrowid
    return {"id": message_id, "recipients": len(numbers), "status": status}
