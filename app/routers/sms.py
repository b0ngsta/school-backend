"""Bulk SMS — compose + history (managers only).

Sending is SIMULATED: messages are stored with recipient counts but no SMS
actually goes out. Later, plug a provider (MSG91 / Twilio) into _send_sms().
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS

router = APIRouter(prefix="/sms", tags=["bulk sms"])

manager = require_roles(*MANAGERS)


class SmsCreate(BaseModel):
    recipient_group: str = Field(min_length=1)  # 'all_parents' | 'all_teachers' | 'all_staff' | 'section:<id>'
    message: str = Field(min_length=1, max_length=1000)
    template: str | None = None


def _send_sms(numbers: list[str], message: str) -> bool:
    """STUB — integrate MSG91/Twilio here later. Returns success."""
    return True


def _resolve_recipients(cur, group: str) -> tuple[str, list[str]]:
    """Returns (human label, phone numbers)."""
    if group == "all_parents":
        cur.execute(
            "SELECT COALESCE(sp.guardian_phone, u.phone) AS phone "
            "FROM users u LEFT JOIN student_profiles sp ON sp.user_id = u.id "
            "WHERE u.user_type = 'student' AND u.is_active = 1"
        )
        return "All Parents", [r["phone"] for r in cur.fetchall() if r["phone"]]
    if group == "all_teachers":
        cur.execute(
            "SELECT phone FROM users WHERE user_type = 'teacher' AND is_active = 1"
        )
        return "All Teachers", [r["phone"] for r in cur.fetchall() if r["phone"]]
    if group == "all_staff":
        cur.execute(
            "SELECT phone FROM users WHERE user_type != 'student' AND is_active = 1"
        )
        return "All Staff", [r["phone"] for r in cur.fetchall() if r["phone"]]
    if group.startswith("section:"):
        section_id = int(group.split(":", 1)[1])
        cur.execute(
            "SELECT c.name AS class_name, s.name AS section_name FROM sections s "
            "JOIN classes c ON c.id = s.class_id WHERE s.id = %s",
            (section_id,),
        )
        sec = cur.fetchone()
        if not sec:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found")
        cur.execute(
            "SELECT COALESCE(sp.guardian_phone, u.phone) AS phone "
            "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
            "WHERE sp.section_id = %s AND u.is_active = 1",
            (section_id,),
        )
        label = f"{sec['class_name']} — {sec['section_name']} Parents"
        return label, [r["phone"] for r in cur.fetchall() if r["phone"]]
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown recipient group")


@router.get("")
def sms_history(actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT m.*, u.full_name AS sent_by FROM sms_messages m "
            "JOIN users u ON u.id = m.created_by ORDER BY m.created_at DESC LIMIT 100"
        )
        return cur.fetchall()


@router.post("", status_code=201)
def send_sms(body: SmsCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        label, numbers = _resolve_recipients(cur, body.recipient_group)
        ok = _send_sms(numbers, body.message)
        cur.execute(
            "INSERT INTO sms_messages "
            "(recipient_group, template, message, recipients_count, status, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                label, body.template, body.message, len(numbers),
                "sent" if ok else "failed", actor["id"],
            ),
        )
        return {"id": cur.lastrowid, "recipients": len(numbers), "status": "sent" if ok else "failed"}
