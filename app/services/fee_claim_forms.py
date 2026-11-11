"""Fee claim forms (migration 0020) — a parent tells the school "I paid":
what for (one of the child's unpaid fees, or free text), amount, date paid,
method, reference / UTR, a note and ONE file (PDF or photo of the receipt /
bank slip / screenshot). Reviewers accept or decline it:

* the CLASS TEACHER of the section the child was in when the form was sent
  (teacher_assignments role 'class_teacher'), and
* MANAGERS (admin, principal, sub_admin, coordinator) — every form of the school.

Subject teachers and drivers never see them.

Two steps since migration 0021:

1. the class teacher CHECKS the form (accept / decline + note); they may
   correct the details the parent typed (amount, date, method, reference,
   purpose) and change their own decision — until the office has validated;
2. the office (any manager) VALIDATES: confirms or overturns the teacher's
   decision. Only this step is final and only it marks a linked unpaid fee
   as paid + records a transaction (same as the office's "Mark paid"), unless
   the manager unticks it. A manager reviewing a form nobody checked yet does
   both steps at once.

`status` is always the latest decision; `validated_at` says it is final
(`final` / `needs_validation` in the API rows). The parent gets a push at
each step (opens the app's Fees screen, where the claim form lives); the
reviewers get one when a form arrives, the office when a teacher has checked.

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
from app.models.fee_claim_forms import FeeClaimFormAccept, FeeClaimFormDecline, FeeClaimFormEdit
from app.services import attachments, audit, notify, pagination
from app.services.fees import _mark_paid

STATUSES = ("pending", "accepted", "declined", "withdrawn")
TO_VALIDATE = "to_validate"  # list filter: checked by the class teacher, waiting for the office
_TO_VALIDATE_SQL = "ff.status IN ('accepted', 'declined') AND ff.validated_at IS NULL"
MAX_AMOUNT = 10_000_000
ORDER_STAFF = f"ff.status = 'pending' DESC, ({_TO_VALIDATE_SQL}) DESC, ff.created_at DESC, ff.id DESC"
ORDER_MINE = "ff.created_at DESC, ff.id DESC"
NOTIFY_TYPE = "fee_claim_form"

_SELECT = (
    "SELECT ff.id, ff.student_id, ff.class_id, ff.section_id, ff.fee_id, ff.purpose, ff.amount, ff.paid_on, "
    "ff.method, ff.reference_no, ff.note, ff.attachment_path, ff.attachment_name, ff.attachment_type, "
    "ff.status, ff.reviewed_at, ff.review_note, ff.fee_marked_paid, ff.transaction_reference, "
    "ff.validated_at, ff.validation_note, ff.edited_at, ff.created_at, ff.updated_at, "
    "f.title AS fee_title, f.amount AS fee_amount, f.status AS fee_status, "
    "u.full_name AS student_name, u.photo_path, sp.roll_no, sp.admission_no, "
    "c.name AS class_name, s.name AS section_name, "
    "r.full_name AS reviewed_by_name, r.user_type AS reviewed_by_role, "
    "v.full_name AS validated_by_name, ed.full_name AS edited_by_name "
    "FROM fee_claim_forms ff "
    "JOIN users u ON u.id = ff.student_id "
    "LEFT JOIN student_profiles sp ON sp.user_id = ff.student_id "
    "LEFT JOIN fees f ON f.id = ff.fee_id "
    "LEFT JOIN classes c ON c.id = ff.class_id "
    "LEFT JOIN sections s ON s.id = ff.section_id "
    "LEFT JOIN users r ON r.id = ff.reviewed_by "
    "LEFT JOIN users v ON v.id = ff.validated_by "
    "LEFT JOIN users ed ON ed.id = ff.edited_by "
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
        decided = row.get("status") in ("accepted", "declined")
        # final = the office has validated it; needs_validation = only the class teacher has checked it
        row["final"] = decided and row.get("validated_at") is not None
        row["needs_validation"] = decided and row.get("validated_at") is None
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
            if db.one("SELECT id FROM fee_claim_forms WHERE fee_id = %s AND (status = 'pending' "
                      "OR (status = 'accepted' AND validated_at IS NULL)) LIMIT 1", (fee_id,)):
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
    if status_filter and status_filter not in (*STATUSES, TO_VALIDATE):
        raise BadRequest(f"status_filter must be one of: {', '.join((*STATUSES, TO_VALIDATE))}")
    scope, params = _scope(db, user)
    sql, args = _SELECT + scope, [db.org, *params]
    if status_filter == TO_VALIDATE:
        sql += f" AND {_TO_VALIDATE_SQL}"
    elif status_filter:
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
        f"SUM(ff.status = 'declined') AS declined, SUM({_TO_VALIDATE_SQL}) AS to_validate "
        "FROM fee_claim_forms ff WHERE ff.org_id = %s" + scope,
        [db.org, *params])
    out = {k: int(row[k] or 0) for k in ("pending", "accepted", "declined")}
    out["total"] = sum(out.values())
    # checked by a class teacher, waiting for the office (a subset of accepted + declined)
    out["to_validate"] = int(row["to_validate"] or 0)
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


def _lock_open(db: Db, user: CurrentUser, form_id: int) -> dict:
    """The form, row-locked, while it can still be reviewed: not withdrawn and
    not yet validated by the office."""
    scope, params = _scope(db, user)
    row = db.one("SELECT * FROM fee_claim_forms ff WHERE ff.org_id = %s" + scope + " AND ff.id = %s FOR UPDATE",
                 [db.org, *params, form_id])
    if not row:
        raise NotFound("Claim not found")
    if row["status"] == "withdrawn":
        raise Conflict("This claim was already withdrawn")
    if row["validated_at"] is not None:
        raise Conflict(f"This claim was already {row['status']} by the office")
    return row


def _note(text: str | None) -> str | None:
    return (text or "").strip() or None


def _decide(db: Db, user: CurrentUser, form_id: int, decision: str, note: str | None,
            mark_fee_paid: bool = False) -> dict:
    """accept / decline. A class teacher's decision is a first check (it can be
    changed until the office validates); a manager's is final."""
    with db.transaction():
        form = _lock_open(db, user, form_id)
        final = _is_manager(user)
        changed = form["status"] != decision
        reference, marked = None, False
        if final:
            if decision == "accepted" and form["fee_id"] is not None and mark_fee_paid:
                fee = db.one("SELECT * FROM fees WHERE id = %s AND org_id = %s FOR UPDATE", (form["fee_id"], db.org))
                if fee and fee["status"] != "paid":
                    reference = _mark_paid(db, fee, form["method"])
                    marked = reference is not None
            if form["status"] == "pending" or form["reviewed_by"] is None:
                # nobody checked it before: the manager is reviewer and validator in one
                db.execute(
                    "UPDATE fee_claim_forms SET status = %s, reviewed_by = %s, reviewed_at = NOW(), review_note = %s, "
                    "validated_by = %s, validated_at = NOW(), validation_note = NULL, fee_marked_paid = %s, "
                    "transaction_reference = %s WHERE id = %s",
                    (decision, user.id, note, user.id, int(marked), reference, form_id))
            else:
                db.execute(
                    "UPDATE fee_claim_forms SET status = %s, validated_by = %s, validated_at = NOW(), "
                    "validation_note = %s, fee_marked_paid = %s, transaction_reference = %s WHERE id = %s",
                    (decision, user.id, note, int(marked), reference, form_id))
        else:
            db.execute(
                "UPDATE fee_claim_forms SET status = %s, reviewed_by = %s, reviewed_at = NOW(), review_note = %s "
                "WHERE id = %s", (decision, user.id, note, form_id))
        audit.record(db, user, "fee_claim_form.accept" if decision == "accepted" else "fee_claim_form.decline",
                     "fee_claim_form", form_id,
                     {"student_id": form["student_id"], "amount": form["amount"], "fee_id": form["fee_id"],
                      "step": "office" if final else "class_teacher", "before": form["status"],
                      "fee_marked_paid": marked, "reference": reference, "note": note})
        _tell(db, user, form, decision, note, final=final, changed=changed, marked=marked)
    return get_staff(db, user, form_id)


def _tell(db: Db, user: CurrentUser, form: dict, decision: str, note: str | None, *, final: bool,
          changed: bool, marked: bool) -> None:
    what = f"{form['purpose']} {notify.inr(form['amount'])}"
    data = {"route": "Fees", "params": {"id": form["id"]}}
    if final:
        if decision == "accepted":
            text = "The fee is marked as paid." if marked else None
            if note:
                text = f"{text} {note}" if text else note
            notify.notify(db, [form["student_id"]], NOTIFY_TYPE, f"✅ Fee claim accepted: {what}", text, data=data)
        else:
            notify.notify(db, [form["student_id"]], NOTIFY_TYPE, f"Fee claim declined: {what}",
                          note or "Please contact the school.", data=data)
        return
    if not changed:
        return  # the teacher only reworded the note
    if decision == "accepted":
        notify.notify(db, [form["student_id"]], NOTIFY_TYPE, f"Fee claim checked by the class teacher: {what}",
                      "Waiting for the school office to confirm it.", data=data)
    else:
        notify.notify(db, [form["student_id"]], NOTIFY_TYPE, f"Fee claim not accepted by the class teacher: {what}",
                      note or "The school office will look at it.", data=data)
    student = db.scalar("SELECT full_name FROM users WHERE id = %s", (form["student_id"],)) or "A student"
    verb = "accepted" if decision == "accepted" else "declined"
    notify.notify(db, notify.manager_ids(db), NOTIFY_TYPE,
                  f"🧾 Fee claim to validate: {student} — {notify.inr(form['amount'])}",
                  f"The class teacher {verb} it: {form['purpose']}",
                  data={"route": "FeeClaimForms", "params": {"id": form["id"]}})


def accept(db: Db, user: CurrentUser, form_id: int, body: FeeClaimFormAccept) -> dict:
    return _decide(db, user, form_id, "accepted", _note(body.note), body.mark_fee_paid)


def decline(db: Db, user: CurrentUser, form_id: int, body: FeeClaimFormDecline) -> dict:
    return _decide(db, user, form_id, "declined", _note(body.note))


def edit(db: Db, user: CurrentUser, form_id: int, body: FeeClaimFormEdit) -> dict:
    """A reviewer corrects what the parent typed (amount, date paid, method,
    reference, purpose) — until the office has validated the form."""
    changes = body.model_dump(exclude_unset=True)
    with db.transaction():
        form = _lock_open(db, user, form_id)
        new: dict = {}
        if changes.get("amount") is not None:
            if not (0 < changes["amount"] <= MAX_AMOUNT):
                raise BadRequest("Enter the amount that was paid")
            new["amount"] = round(float(changes["amount"]), 2)
        if changes.get("paid_on") is not None:
            if changes["paid_on"] > school_today():
                raise BadRequest("The payment date can't be in the future")
            new["paid_on"] = changes["paid_on"]
        if changes.get("method") is not None:
            new["method"] = changes["method"]
        if "reference_no" in changes:
            new["reference_no"] = (changes["reference_no"] or "").strip()[:100] or None
        if "purpose" in changes:
            purpose = (changes["purpose"] or "").strip()[:150]
            if not purpose:
                raise BadRequest("Say what this payment was for")
            new["purpose"] = purpose
        before = {k: form[k] for k in new}
        new = {k: v for k, v in new.items()
               if (float(before[k]) != v if k == "amount" else before[k] != v)}
        if new:
            sets = ", ".join(f"{k} = %s" for k in new)
            db.execute(f"UPDATE fee_claim_forms SET {sets}, edited_by = %s, edited_at = NOW() WHERE id = %s",
                       (*new.values(), user.id, form_id))
            audit.record(db, user, "fee_claim_form.update", "fee_claim_form", form_id,
                         {"student_id": form["student_id"], "before": {k: before[k] for k in new}, "after": new})
    return get_staff(db, user, form_id)
