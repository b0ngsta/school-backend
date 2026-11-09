"""Fee claim forms (migration 0020) — a parent tells the school "I paid":
what for (one of the child's unpaid fees, or free text), amount, date paid,
method, reference / UTR, a note and ONE file (PDF or photo of the receipt /
bank slip / screenshot). Reviewers accept or decline it:

* the CLASS TEACHER of the section the child was in when the form was sent
  (teacher_assignments role 'class_teacher'), and
* MANAGERS (admin, principal, sub_admin, coordinator) — every form of the school.

Subject teachers and drivers never see them. Accepting a form linked to an
unpaid fee also marks that fee paid and records a transaction (same as the
office's "Mark paid"), unless the reviewer unticks it. The parent gets a
push on accept / decline; the reviewers get one when a form arrives.

Separate from the older per-fee payment proofs (services/fees.py
`submit_claim`, table fee_claims), which are unchanged.
"""
from __future__ import annotations

from datetime import date

from fastapi import UploadFile

from app.core.clock import school_today
from app.core.errors import BadRequest, Conflict, NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.fee_claim_forms import FeeClaimFormAccept, FeeClaimFormDecline
from app.services import attachments, audit, notify, pagination
from app.services.fees import _mark_paid

STATUSES = ("pending", "accepted", "declined", "withdrawn")
MAX_AMOUNT = 10_000_000
ORDER_STAFF = "ff.status = 'pending' DESC, ff.created_at DESC, ff.id DESC"
ORDER_MINE = "ff.created_at DESC, ff.id DESC"
NOTIFY_TYPE = "fee_claim_form"

_SELECT = (
    "SELECT ff.id, ff.student_id, ff.class_id, ff.section_id, ff.fee_id, ff.purpose, ff.amount, ff.paid_on, "
    "ff.method, ff.reference_no, ff.note, ff.attachment_path, ff.attachment_name, ff.attachment_type, "
    "ff.status, ff.reviewed_at, ff.review_note, ff.fee_marked_paid, ff.transaction_reference, "
    "ff.created_at, ff.updated_at, "
    "f.title AS fee_title, f.amount AS fee_amount, f.status AS fee_status, "
    "u.full_name AS student_name, u.photo_path, sp.roll_no, sp.admission_no, "
    "c.name AS class_name, s.name AS section_name, "
    "r.full_name AS reviewed_by_name, r.user_type AS reviewed_by_role "
    "FROM fee_claim_forms ff "
    "JOIN users u ON u.id = ff.student_id "
    "LEFT JOIN student_profiles sp ON sp.user_id = ff.student_id "
    "LEFT JOIN fees f ON f.id = ff.fee_id "
    "LEFT JOIN classes c ON c.id = ff.class_id "
    "LEFT JOIN sections s ON s.id = ff.section_id "
    "LEFT JOIN users r ON r.id = ff.reviewed_by "
    "WHERE ff.org_id = %s"
)
# sections the teacher is class teacher of (the only forms a teacher reviews)
_CLASS_TEACHER_SECTIONS = (
    "SELECT ta.section_id FROM teacher_assignments ta "
    "WHERE ta.org_id = %s AND ta.teacher_id = %s AND ta.role = 'class_teacher'"
)


def _is_manager(user: CurrentUser) -> bool:
    return user.user_type in MANAGERS


def _shape(row: dict | None) -> dict | None:
    if row is not None:
        row["fee_marked_paid"] = bool(row.get("fee_marked_paid"))
    return row


def _label(row: dict) -> str:
    if row.get("class_name") and row.get("section_name"):
        return f"{row['class_name']} — {row['section_name']}"
    return row.get("class_name") or ""


# ------------------------------------------------------------------ parents

def submit(
    db: Db,
    student: CurrentUser,
    *,
    file: UploadFile,
    amount: float,
    paid_on: date,
    method: str,
    purpose: str | None,
    fee_id: int | None,
    reference_no: str | None,
    note: str | None,
) -> dict:
    if not (0 < amount <= MAX_AMOUNT):
        raise BadRequest("Enter the amount you paid")
    if paid_on > school_today():
        raise BadRequest("The payment date can't be in the future")
    purpose = (purpose or "").strip()[:150]
    with db.transaction():
        me = db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s AND org_id = %s",
                    (student.id, db.org)) or {}
        if fee_id is not None:
            fee = db.one("SELECT * FROM fees WHERE id = %s AND student_id = %s AND org_id = %s FOR UPDATE",
                         (fee_id, student.id, db.org))
            if not fee:
                raise NotFound("Fee not found")
            if fee["status"] == "paid":
                raise Conflict("This fee is already paid")
            if db.one("SELECT id FROM fee_claim_forms WHERE fee_id = %s AND status = 'pending' LIMIT 1", (fee_id,)):
                raise Conflict("A claim form for this fee is already waiting for review")
            if db.one("SELECT id FROM fee_claims WHERE fee_id = %s AND status = 'pending' LIMIT 1", (fee_id,)):
                raise Conflict("A payment proof for this fee is already under review")
            purpose = purpose or fee["title"]
        if not purpose:
            raise BadRequest("Say what this payment was for (e.g. Term 2 tuition fee)")
        file_ = attachments.save(db, file, "fee-claims", "payment-proof")
        form_id = db.execute(
            "INSERT INTO fee_claim_forms (org_id, student_id, class_id, section_id, fee_id, purpose, amount, "
            "paid_on, method, reference_no, note, attachment_path, attachment_name, attachment_type) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, student.id, me.get("class_id"), me.get("section_id"), fee_id, purpose, round(amount, 2),
             paid_on, method, (reference_no or "").strip()[:100] or None, (note or "").strip()[:500] or None,
             *file_.columns()),
        ).lastrowid
        row = _shape(db.one(_SELECT + " AND ff.id = %s", (db.org, form_id)))
        _notify_reviewers(db, row)
    return row


def _notify_reviewers(db: Db, row: dict) -> None:
    """The class teacher(s) of the child's section + every manager."""
    teachers = [r["teacher_id"] for r in db.all(
        "SELECT DISTINCT ta.teacher_id FROM teacher_assignments ta JOIN users u ON u.id = ta.teacher_id "
        "WHERE ta.org_id = %s AND ta.section_id = %s AND ta.role = 'class_teacher' AND u.is_active = 1",
        (db.org, row["section_id"]))] if row.get("section_id") else []
    who = row["student_name"] + (f" ({_label(row)})" if _label(row) else "")
    notify.notify(db, teachers + notify.manager_ids(db), NOTIFY_TYPE,
                  f"🧾 Fee claim: {who} — {notify.inr(row['amount'])}", row["purpose"],
                  data={"route": "FeeClaimForms", "params": {"id": row["id"]}})


def list_mine(db: Db, student: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    result = pagination.fetch(db, _SELECT + " AND ff.student_id = %s", (db.org, student.id), ORDER_MINE, page)
    for row in result.rows:
        _shape(row)
    return result


def get_mine(db: Db, student: CurrentUser, form_id: int) -> dict:
    row = db.one(_SELECT + " AND ff.student_id = %s AND ff.id = %s", (db.org, student.id, form_id))
    if not row:
        raise NotFound("Claim not found")
    return _shape(row)


def withdraw(db: Db, student: CurrentUser, form_id: int) -> dict:
    """The parent takes back a form nobody has reviewed yet (kept, marked withdrawn)."""
    with db.transaction():
        row = db.one("SELECT id, status FROM fee_claim_forms WHERE id = %s AND org_id = %s AND student_id = %s "
                     "FOR UPDATE", (form_id, db.org, student.id))
        if not row:
            raise NotFound("Claim not found")
        if row["status"] != "pending":
            raise Conflict(f"This claim was already {row['status']}")
        db.execute("UPDATE fee_claim_forms SET status = 'withdrawn' WHERE id = %s", (form_id,))
    return get_mine(db, student, form_id)


# ------------------------------------------------------------------ reviewers

def _scope(db: Db, user: CurrentUser) -> tuple[str, list]:
    """WHERE fragment (after `ff.org_id = %s`) limiting forms to what the caller reviews."""
    if _is_manager(user):
        return "", []
    return f" AND ff.section_id IN ({_CLASS_TEACHER_SECTIONS})", [db.org, user.id]


def list_staff(db: Db, user: CurrentUser, status_filter: str | None, section_id: int | None,
               page: pagination.Page | None = None) -> pagination.PageResult:
    if status_filter and status_filter not in STATUSES:
        raise BadRequest(f"status_filter must be one of: {', '.join(STATUSES)}")
    scope, params = _scope(db, user)
    sql, args = _SELECT + scope, [db.org, *params]
    if status_filter:
        sql += " AND ff.status = %s"
        args.append(status_filter)
    else:
        sql += " AND ff.status <> 'withdrawn'"
    if section_id is not None:
        sql += " AND ff.section_id = %s"
        args.append(section_id)
    result = pagination.fetch(db, sql, args, ORDER_STAFF, page)
    for row in result.rows:
        _shape(row)
    return result


def stats(db: Db, user: CurrentUser) -> dict:
    """Counts the caller can see + whether they review anything at all
    (teachers who are nobody's class teacher see `can_review: false`)."""
    scope, params = _scope(db, user)
    row = db.one(
        "SELECT SUM(ff.status = 'pending') AS pending, SUM(ff.status = 'accepted') AS accepted, "
        "SUM(ff.status = 'declined') AS declined FROM fee_claim_forms ff WHERE ff.org_id = %s" + scope,
        [db.org, *params])
    out = {k: int(row[k] or 0) for k in ("pending", "accepted", "declined")}
    out["total"] = sum(out.values())
    if _is_manager(user):
        out["can_review"], out["sections"] = True, []
    else:
        sections = db.all(
            "SELECT s.id AS section_id, c.name AS class_name, s.name AS section_name "
            "FROM teacher_assignments ta JOIN sections s ON s.id = ta.section_id JOIN classes c ON c.id = s.class_id "
            "WHERE ta.org_id = %s AND ta.teacher_id = %s AND ta.role = 'class_teacher' ORDER BY c.name, s.name",
            (db.org, user.id))
        for s in sections:
            s["label"] = _label(s)
        out["can_review"], out["sections"] = bool(sections), sections
    return out


def get_staff(db: Db, user: CurrentUser, form_id: int) -> dict:
    scope, params = _scope(db, user)
    row = db.one(_SELECT + scope + " AND ff.id = %s", [db.org, *params, form_id])
    if not row:
        raise NotFound("Claim not found")
    return _shape(row)


def _lock_pending(db: Db, user: CurrentUser, form_id: int) -> dict:
    scope, params = _scope(db, user)
    row = db.one("SELECT * FROM fee_claim_forms ff WHERE ff.org_id = %s" + scope + " AND ff.id = %s FOR UPDATE",
                 [db.org, *params, form_id])
    if not row:
        raise NotFound("Claim not found")
    if row["status"] != "pending":
        raise Conflict(f"This claim was already {row['status']}")
    return row


def accept(db: Db, user: CurrentUser, form_id: int, body: FeeClaimFormAccept) -> dict:
    with db.transaction():
        form = _lock_pending(db, user, form_id)
        reference, marked = None, False
        if form["fee_id"] is not None and body.mark_fee_paid:
            fee = db.one("SELECT * FROM fees WHERE id = %s AND org_id = %s FOR UPDATE", (form["fee_id"], db.org))
            if fee and fee["status"] != "paid":
                reference = _mark_paid(db, fee, form["method"])
                marked = reference is not None
        db.execute(
            "UPDATE fee_claim_forms SET status = 'accepted', reviewed_by = %s, reviewed_at = NOW(), "
            "review_note = %s, fee_marked_paid = %s, transaction_reference = %s WHERE id = %s",
            (user.id, (body.note or "").strip() or None, int(marked), reference, form_id))
        audit.record(db, user, "fee_claim_form.accept", "fee_claim_form", form_id,
                     {"student_id": form["student_id"], "amount": form["amount"], "fee_id": form["fee_id"],
                      "fee_marked_paid": marked, "reference": reference})
        text = "The fee is marked as paid." if marked else None
        if body.note and body.note.strip():
            text = f"{text} {body.note.strip()}" if text else body.note.strip()
        notify.notify(db, [form["student_id"]], NOTIFY_TYPE,
                      f"✅ Fee claim accepted: {form['purpose']} {notify.inr(form['amount'])}", text,
                      data={"route": "FeeClaimForm", "params": {"id": form_id}})
    return get_staff(db, user, form_id)


def decline(db: Db, user: CurrentUser, form_id: int, body: FeeClaimFormDecline) -> dict:
    with db.transaction():
        form = _lock_pending(db, user, form_id)
        note = (body.note or "").strip() or None
        db.execute(
            "UPDATE fee_claim_forms SET status = 'declined', reviewed_by = %s, reviewed_at = NOW(), "
            "review_note = %s WHERE id = %s", (user.id, note, form_id))
        audit.record(db, user, "fee_claim_form.decline", "fee_claim_form", form_id,
                     {"student_id": form["student_id"], "amount": form["amount"], "note": note})
        notify.notify(db, [form["student_id"]], NOTIFY_TYPE,
                      f"Fee claim declined: {form['purpose']} {notify.inr(form['amount'])}",
                      note or "Please contact the school.",
                      data={"route": "FeeClaimForm", "params": {"id": form_id}})
    return get_staff(db, user, form_id)
