"""Fees — per-student fee records; paying creates a transaction.

Fee claims: a parent (via the student login) uploads a payment-proof
screenshot; managers/principal/teachers approve or reject. Approving marks
the fee paid and records a transaction.

Every state change locks the row it changes (SELECT … FOR UPDATE) and only
moves it out of 'pending', so double clicks or two reviewers acting at the
same moment can never record a payment twice.
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import BadRequest, Conflict, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.fees import ClaimReview, FeeCreate, FeeGenerate
from app.services import audit, notify, pagination, school_profile, storage
from app.services.payments import new_reference

# effective status exposes 'overdue' (pending + past due date) without storing it
EFFECTIVE_STATUS = (
    "CASE WHEN f.status = 'pending' AND f.due_date IS NOT NULL AND f.due_date < CURDATE() "
    "THEN 'overdue' ELSE f.status END"
)

CLAIM_SELECT = (
    "SELECT fc.*, f.title AS fee_title, f.amount AS fee_amount, f.status AS fee_status, "
    "f.due_date, u.full_name AS student_name, u.photo_path, sp.roll_no, "
    "c.name AS class_name, s.name AS section_name, r.full_name AS reviewed_by_name "
    "FROM fee_claims fc "
    "JOIN fees f ON f.id = fc.fee_id "
    "JOIN users u ON u.id = fc.student_id "
    "LEFT JOIN student_profiles sp ON sp.user_id = fc.student_id "
    "LEFT JOIN classes c ON c.id = sp.class_id "
    "LEFT JOIN sections s ON s.id = sp.section_id "
    "LEFT JOIN users r ON r.id = fc.reviewed_by"
)


# ------------------------------------------------------------------ fees

def stats(db: Db) -> dict:
    row = db.one(
        f"SELECT "
        f"COALESCE(SUM(CASE WHEN f.status='paid' THEN f.amount END), 0) AS collected, "
        f"COALESCE(SUM(CASE WHEN {EFFECTIVE_STATUS}='pending' THEN f.amount END), 0) AS pending, "
        f"COALESCE(SUM(CASE WHEN {EFFECTIVE_STATUS}='overdue' THEN f.amount END), 0) AS overdue, "
        f"COALESCE(SUM(f.amount), 0) AS total "
        f"FROM fees f WHERE f.org_id = %s",
        (db.org,),
    )
    total, collected = float(row["total"]), float(row["collected"])
    return {
        "collected": collected,
        "pending": float(row["pending"]),
        "overdue": float(row["overdue"]),
        "total": total,
        "collection_rate": round(collected / total * 100, 1) if total else 0.0,
    }


def pending_students(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    """Students with unpaid fees, with pending/overdue totals."""
    return pagination.fetch(
        db,
        f"SELECT u.id AS student_id, u.full_name, u.photo_path, sp.roll_no, "
        f"c.name AS class_name, s.name AS section_name, "
        f"COALESCE(SUM(f.amount), 0) AS pending_total, "
        f"COALESCE(SUM(CASE WHEN {EFFECTIVE_STATUS}='overdue' THEN f.amount END), 0) AS overdue_total, "
        f"COUNT(f.id) AS pending_count "
        f"FROM fees f "
        f"JOIN users u ON u.id = f.student_id "
        f"LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        f"LEFT JOIN classes c ON c.id = sp.class_id "
        f"LEFT JOIN sections s ON s.id = sp.section_id "
        f"WHERE f.org_id = %s AND f.status = 'pending' "
        f"GROUP BY u.id, u.full_name, u.photo_path, sp.roll_no, c.name, s.name",
        (db.org,),
        "overdue_total DESC, pending_total DESC, student_id",
        page,
    )


def overdue_students(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    """Students who have at least one fee past its due date."""
    return pagination.fetch(
        db,
        "SELECT u.id AS student_id, u.full_name, u.photo_path, sp.roll_no, "
        "c.name AS class_name, s.name AS section_name, "
        "COALESCE(SUM(f.amount), 0) AS overdue_total, COUNT(f.id) AS overdue_count, "
        "MIN(f.due_date) AS oldest_due "
        "FROM fees f "
        "JOIN users u ON u.id = f.student_id "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN classes c ON c.id = sp.class_id "
        "LEFT JOIN sections s ON s.id = sp.section_id "
        "WHERE f.org_id = %s AND f.status = 'pending' AND f.due_date IS NOT NULL AND f.due_date < CURDATE() "
        "GROUP BY u.id, u.full_name, u.photo_path, sp.roll_no, c.name, s.name",
        (db.org,),
        "overdue_total DESC, oldest_due, student_id",
        page,
    )


def student_history(db: Db, student_id: int) -> dict:
    """One student's fees and payments (newest first) with totals."""
    student = db.one(
        "SELECT u.id AS student_id, u.full_name, u.photo_path, u.is_active, u.left_on, sp.roll_no, "
        "sp.admission_no, sp.father_name, sp.guardian_phone, c.name AS class_name, s.name AS section_name "
        "FROM users u LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN classes c ON c.id = sp.class_id LEFT JOIN sections s ON s.id = sp.section_id "
        "WHERE u.id = %s AND u.org_id = %s AND u.user_type = 'student'", (student_id, db.org))
    if not student:
        raise NotFound("Student not found")
    fees = db.all(
        f"SELECT f.id, f.title, f.amount, f.due_date, f.status, f.paid_date, f.method, f.created_at, "
        f"{EFFECTIVE_STATUS} AS effective_status FROM fees f "
        "WHERE f.org_id = %s AND f.student_id = %s ORDER BY f.created_at DESC, f.id DESC", (db.org, student_id))
    payments = db.all(
        "SELECT t.id, t.reference, t.fee_id, t.amount, t.method, t.status, t.created_at, f.title AS fee_title "
        "FROM transactions t LEFT JOIN fees f ON f.id = t.fee_id "
        "WHERE t.org_id = %s AND t.student_id = %s ORDER BY t.created_at DESC, t.id DESC", (db.org, student_id))
    totals = {"paid": 0.0, "pending": 0.0, "overdue": 0.0}
    for f in fees:
        totals[f["effective_status"]] += float(f["amount"])
    totals["billed"] = round(sum(totals.values()), 2)
    return {"student": student, "totals": totals, "fees": fees, "payments": payments}


# -------------------------------------------- class fee in parts (instalments)

def _money(value) -> float:
    return round(float(value or 0), 2)


def class_plan(db: Db, class_id: int) -> dict:
    """The class's fee for the current session and the parts billed so far."""
    cls = db.one("SELECT id, name, fee_amount FROM classes WHERE id = %s AND org_id = %s", (class_id, db.org))
    if not cls:
        raise NotFound("Class not found")
    session = school_profile.current_session(db)["label"]
    parts = db.all(
        "SELECT i.id, i.title, i.amount, i.due_date, i.created_at, "
        "COUNT(f.id) AS students, COALESCE(SUM(f.status = 'paid'), 0) AS paid "
        "FROM class_fee_installments i LEFT JOIN fees f ON f.installment_id = i.id "
        "WHERE i.org_id = %s AND i.class_id = %s AND i.session = %s "
        "GROUP BY i.id, i.title, i.amount, i.due_date, i.created_at ORDER BY i.created_at, i.id",
        (db.org, class_id, session))
    for p in parts:
        p["amount"], p["students"], p["paid"] = _money(p["amount"]), int(p["students"]), int(p["paid"])
    allotted = _money(sum(p["amount"] for p in parts))
    class_fee = _money(cls["fee_amount"]) if cls["fee_amount"] else None
    return {
        "class_id": cls["id"], "class_name": cls["name"], "session": session, "class_fee": class_fee,
        "allotted": allotted,
        "remaining": _money(class_fee - allotted) if class_fee is not None else None,
        "installments": parts,
    }


def _installment(db: Db, actor: CurrentUser, body: FeeGenerate, cls: dict) -> int:
    """The instalment row for this title (made now, or the existing one when
    the same part is generated again for students who joined later)."""
    session = school_profile.current_session(db)["label"]
    total = _money(cls["fee_amount"])
    amount = _money(body.amount)
    same = db.one("SELECT id, amount FROM class_fee_installments WHERE class_id = %s AND session = %s "
                  "AND title = %s FOR UPDATE", (body.class_id, session, body.title))
    if same:
        if abs(_money(same["amount"]) - amount) > 0.004:
            raise Conflict(f'"{body.title}" is already billed to this class as {notify.inr(same["amount"])}. '
                           "Use the same amount to add it for new students, or pick another title.")
        return same["id"]
    allotted = _money(db.scalar("SELECT COALESCE(SUM(amount), 0) FROM class_fee_installments "
                                "WHERE class_id = %s AND session = %s", (body.class_id, session)))
    left = _money(total - allotted)
    if amount > left + 0.004:
        if left <= 0:
            text = (f"The whole fee of {cls['name']} ({notify.inr(total)}) is already billed for {session}. "
                    "Raise the class fee on the Classes page if it has changed.")
        else:
            text = (f"Only {notify.inr(left)} of {cls['name']}'s fee ({notify.inr(total)}) is left to bill: "
                    f"{notify.inr(allotted)} is already billed.")
        raise BadRequest(text, extra={"code": "over_class_fee", "class_fee": total, "allotted": allotted,
                                      "remaining": max(left, 0.0)})
    return db.execute(
        "INSERT INTO class_fee_installments (org_id, class_id, session, title, amount, due_date, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (db.org, body.class_id, session, body.title, amount, body.due_date, actor.id)).lastrowid


def delete_installment(db: Db, actor: CurrentUser, installment_id: int) -> dict:
    """Take back a part billed by mistake: its unpaid fee records go with it.
    Refused once any student has paid it or a payment claim is open on it."""
    with db.transaction():
        part = db.one("SELECT * FROM class_fee_installments WHERE id = %s AND org_id = %s FOR UPDATE",
                      (installment_id, db.org))
        if not part:
            raise NotFound("Fee part not found")
        fee_ids = [r["id"] for r in db.all("SELECT id FROM fees WHERE installment_id = %s AND org_id = %s FOR UPDATE",
                                           (installment_id, db.org))]
        if fee_ids:
            ph = ",".join(["%s"] * len(fee_ids))
            paid = int(db.scalar(f"SELECT COUNT(*) FROM fees WHERE id IN ({ph}) AND status = 'paid'", fee_ids) or 0)
            if paid:
                who = "1 student has" if paid == 1 else f"{paid} students have"
                raise Conflict(f'"{part["title"]}" can\'t be removed: {who} already paid it. '
                               "Mark those fees unpaid first if they were marked by mistake.",
                               extra={"code": "has_records", "records": {"paid": paid}})
            claims = int(db.scalar(
                f"SELECT (SELECT COUNT(*) FROM fee_claims WHERE fee_id IN ({ph}) AND status IN ('pending', 'approved'))"
                f" + (SELECT COUNT(*) FROM fee_claim_forms WHERE fee_id IN ({ph}) "
                "AND status IN ('pending', 'accepted') AND validated_at IS NULL)", [*fee_ids, *fee_ids]) or 0)
            if claims:
                raise Conflict(f'"{part["title"]}" can\'t be removed: parents have sent payment claims for it. '
                               "Review those claims first.",
                               extra={"code": "has_records", "records": {"payment_claims": claims}})
            db.execute(f"DELETE FROM fee_claims WHERE fee_id IN ({ph}) AND status = 'rejected'", fee_ids)
            db.execute(f"DELETE FROM fees WHERE id IN ({ph})", fee_ids)
        db.execute("DELETE FROM class_fee_installments WHERE id = %s", (installment_id,))
        audit.record(db, actor, "fee.installment_delete", "class", part["class_id"],
                     {"title": part["title"], "amount": part["amount"], "fees_removed": len(fee_ids)})
    return {"deleted": installment_id, "fees_removed": len(fee_ids)}


def generate_class_fees(db: Db, actor: CurrentUser, body: FeeGenerate) -> dict:
    """Bill every active student of a class. With `amount` it is one part
    (instalment) of the class's fee: the parts of a session may add up to at
    most the class's fee_amount. Without it (older apps) the whole class fee is
    billed, as before. Re-running with the same title only adds the fee for
    students who don't have it yet."""
    with db.transaction():
        # the row lock serialises concurrent generate calls for the same class
        cls = db.one("SELECT name, fee_amount FROM classes WHERE id = %s AND org_id = %s FOR UPDATE",
                     (body.class_id, db.org))
        if not cls:
            raise NotFound("Class not found")
        if not cls["fee_amount"]:
            raise BadRequest("This class has no fee amount set — edit the class first")
        installment_id = _installment(db, actor, body, cls) if body.amount is not None else None
        amount = _money(body.amount) if body.amount is not None else cls["fee_amount"]
        students = db.scalar(
            "SELECT COUNT(*) FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
            "WHERE sp.class_id = %s AND u.is_active = 1",
            (body.class_id,),
        )
        new_for = [r["user_id"] for r in db.all(
            "SELECT sp.user_id FROM student_profiles sp "
            "JOIN users u ON u.id = sp.user_id "
            "WHERE sp.class_id = %s AND u.is_active = 1 "
            "AND NOT EXISTS (SELECT 1 FROM fees f WHERE f.student_id = sp.user_id AND f.title = %s)",
            (body.class_id, body.title),
        )]
        created = db.execute(
            "INSERT INTO fees (org_id, student_id, title, amount, due_date, installment_id) "
            "SELECT %s, sp.user_id, %s, %s, %s, %s FROM student_profiles sp "
            "JOIN users u ON u.id = sp.user_id "
            "WHERE sp.class_id = %s AND u.is_active = 1 "
            "AND NOT EXISTS (SELECT 1 FROM fees f WHERE f.student_id = sp.user_id AND f.title = %s) "
            "ORDER BY sp.user_id",
            (db.org, body.title, amount, body.due_date, installment_id, body.class_id, body.title),
        ).rowcount
        if created:
            audit.record(db, actor, "fee.generate", "class", body.class_id,
                         {"title": body.title, "created": created, "amount": amount,
                          "installment_id": installment_id})
            _notify_new_fee(db, new_for, body.title, amount, body.due_date)
    out = {"created": created, "students": int(students or 0), "amount": float(amount)}
    if installment_id is not None:
        out["installment_id"] = installment_id
        out["plan"] = class_plan(db, body.class_id)
    return out


def list_fees(db: Db, status_filter: str | None, student_id: int | None,
              page: pagination.Page | None = None) -> pagination.PageResult:
    sql = (
        f"SELECT f.*, {EFFECTIVE_STATUS} AS effective_status, "
        "u.full_name AS student_name, sp.roll_no, c.name AS class_name, s.name AS section_name "
        "FROM fees f "
        "JOIN users u ON u.id = f.student_id "
        "LEFT JOIN student_profiles sp ON sp.user_id = f.student_id "
        "LEFT JOIN classes c ON c.id = sp.class_id "
        "LEFT JOIN sections s ON s.id = sp.section_id"
    )
    where, params = ["f.org_id = %s"], [db.org]
    if student_id is not None:
        where.append("f.student_id = %s")
        params.append(student_id)
    if status_filter:
        where.append(f"({EFFECTIVE_STATUS}) = %s")
        params.append(status_filter)
    sql += " WHERE " + " AND ".join(where)
    return pagination.fetch(db, sql, params, "f.created_at DESC, f.id DESC", page)


def create_fee(db: Db, actor: CurrentUser, body: FeeCreate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM users WHERE id = %s AND org_id = %s AND user_type = 'student'",
                      (body.student_id, db.org)):
            raise NotFound("Student not found")
        fee_id = db.execute(
            "INSERT INTO fees (org_id, student_id, title, amount, due_date) VALUES (%s, %s, %s, %s, %s)",
            (db.org, body.student_id, body.title, body.amount, body.due_date),
        ).lastrowid
        audit.record(db, actor, "fee.create", "fee", fee_id, body.model_dump())
        _notify_new_fee(db, [body.student_id], body.title, body.amount, body.due_date)
    return {"id": fee_id}


def _notify_new_fee(db: Db, student_ids: list[int], title: str, amount, due_date) -> None:
    text = f"New fee: {title} — {notify.inr(amount)}"
    if due_date:
        text += f" due {notify.day_month(due_date)}"
    notify.notify(db, student_ids, "fee", text, data=notify.route("fee", student=True))


def _notify_paid(db: Db, fee: dict) -> None:
    notify.notify(db, [fee["student_id"]], "fee", f"Payment received: {fee['title']} {notify.inr(fee['amount'])}",
                  data=notify.route("fee", student=True))


def _mark_paid(db: Db, fee: dict, method: str) -> str | None:
    """Pending -> paid plus a success transaction. Returns the transaction
    reference, or None if the fee was already paid."""
    changed = db.execute(
        "UPDATE fees SET status='paid', paid_date=CURDATE(), method=%s WHERE id=%s AND status='pending'",
        (method, fee["id"]),
    ).rowcount
    if not changed:
        return None
    reference = new_reference()
    db.execute(
        "INSERT INTO transactions (org_id, reference, student_id, fee_id, amount, method, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'success')",
        (fee["org_id"], reference, fee["student_id"], fee["id"], fee["amount"], method),
    )
    return reference


def pay_fee(db: Db, actor: CurrentUser, fee_id: int, method: str) -> dict:
    with db.transaction():
        fee = db.one("SELECT * FROM fees WHERE id = %s AND org_id = %s FOR UPDATE", (fee_id, db.org))
        if not fee:
            raise NotFound("Fee not found")
        reference = _mark_paid(db, fee, method) if fee["status"] != "paid" else None
        if reference is None:
            raise Conflict("Already paid")
        audit.record(db, actor, "fee.pay", "fee", fee_id,
                     {"method": method, "amount": fee["amount"], "reference": reference})
        _notify_paid(db, fee)
    return {"paid": fee_id, "reference": reference}


def unpay_fee(db: Db, actor: CurrentUser, fee_id: int, reason: str | None = None) -> dict:
    """Undo "paid" (marked by mistake, bounced cheque…): the fee is unpaid
    again; its payment row is kept and marked `reversed`, so the day's
    collection no longer counts it and the history shows what happened."""
    reason = (reason or "").strip() or None
    with db.transaction():
        fee = db.one("SELECT * FROM fees WHERE id = %s AND org_id = %s FOR UPDATE", (fee_id, db.org))
        if not fee:
            raise NotFound("Fee not found")
        if fee["status"] != "paid":
            raise Conflict("This fee is not marked as paid")
        db.execute("UPDATE fees SET status = 'pending', paid_date = NULL, method = NULL WHERE id = %s", (fee_id,))
        reversed_n = db.execute(
            "UPDATE transactions SET status = 'reversed' WHERE org_id = %s AND fee_id = %s AND status = 'success'",
            (db.org, fee_id)).rowcount
        audit.record(db, actor, "fee.unpay", "fee", fee_id,
                     {"student_id": fee["student_id"], "title": fee["title"], "amount": fee["amount"],
                      "method": fee["method"], "paid_date": fee["paid_date"],
                      "transactions_reversed": reversed_n, "reason": reason})
        notify.notify(db, [fee["student_id"]], "fee",
                      f"Payment undone: {fee['title']} {notify.inr(fee['amount'])} is unpaid again", reason,
                      data=notify.route("fee", student=True))
    return {"unpaid": fee_id, "transactions_reversed": reversed_n}


def delete_fee(db: Db, actor: CurrentUser, fee_id: int) -> dict:
    with db.transaction():
        fee = db.one("SELECT * FROM fees WHERE id = %s AND org_id = %s FOR UPDATE", (fee_id, db.org))
        if not fee:
            raise NotFound("Fee not found")
        # money records are never erased (migration 0017 makes the database refuse it too)
        if fee["status"] == "paid":
            raise Conflict("This fee has been paid, so it can't be deleted: the payment record has to stay.",
                           extra={"code": "has_records", "records": {"paid": 1}})
        open_claims = int(db.scalar(
            "SELECT COUNT(*) FROM fee_claims WHERE fee_id = %s AND status IN ('pending', 'approved')", (fee_id,)
        ) or 0)
        if open_claims:
            raise Conflict("A parent has sent a payment proof for this fee. Approve or reject it before deleting "
                           "the fee.", extra={"code": "has_records", "records": {"payment_claims": open_claims}})
        # rejected proofs go with the fee, as before
        db.execute("DELETE FROM fee_claims WHERE fee_id = %s AND status = 'rejected'", (fee_id,))
        db.execute("DELETE FROM fees WHERE id = %s", (fee_id,))
        audit.record(db, actor, "fee.delete", "fee", fee_id,
                     {k: fee[k] for k in ("student_id", "title", "amount", "status")})
    return {"deleted": fee_id}


# --------------------------------------------- claims (parent-submitted proof)

def submit_claim(
    db: Db,
    student: CurrentUser,
    fee_id: int,
    file: UploadFile,
    method: str,
    reference_no: str | None,
    note: str | None,
) -> dict:
    """Parent (student login) submits a payment-proof screenshot for their fee."""
    with db.transaction():
        fee = db.one("SELECT * FROM fees WHERE id = %s AND student_id = %s AND org_id = %s FOR UPDATE",
                     (fee_id, student.id, db.org))
        if not fee:
            raise NotFound("Fee not found")
        if fee["status"] == "paid":
            raise Conflict("This fee is already paid")
        if db.one("SELECT id FROM fee_claims WHERE fee_id = %s AND status = 'pending'", (fee_id,)):
            raise Conflict("A claim for this fee is already under review")
        stored = storage.save_upload(db, file, "claims", storage.IMAGE_TYPES, max_mb=10)
        claim_id = db.execute(
            "INSERT INTO fee_claims "
            "(org_id, fee_id, student_id, amount, method, reference_no, note, screenshot_path, screenshot_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, fee_id, student.id, fee["amount"], method,
             (reference_no or "").strip()[:100] or None,
             (note or "").strip()[:255] or None,
             stored.path, stored.original_name),
        ).lastrowid
    return {"id": claim_id, "status": "pending"}


def my_claims(db: Db, student: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(db, CLAIM_SELECT + " WHERE fc.org_id = %s AND fc.student_id = %s", (db.org, student.id),
                            "fc.created_at DESC, fc.id DESC", page)


def claim_stats(db: Db) -> dict:
    row = db.one(
        "SELECT COUNT(*) AS total, "
        "SUM(status='pending') AS pending, "
        "SUM(status='approved') AS approved, "
        "SUM(status='rejected') AS rejected "
        "FROM fee_claims WHERE org_id = %s",
        (db.org,),
    )
    return {k: int(v or 0) for k, v in row.items()}


def list_claims(db: Db, status_filter: str | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql, params = CLAIM_SELECT + " WHERE fc.org_id = %s", (db.org,)
    if status_filter:
        sql += " AND fc.status = %s"
        params = (db.org, status_filter)
    return pagination.fetch(db, sql, params, "fc.status = 'pending' DESC, fc.created_at DESC, fc.id DESC", page)


def _lock_pending_claim(db: Db, claim_id: int) -> dict:
    claim = db.one("SELECT * FROM fee_claims WHERE id = %s AND org_id = %s FOR UPDATE", (claim_id, db.org))
    if not claim:
        raise NotFound("Claim not found")
    if claim["status"] != "pending":
        raise Conflict(f"Claim already {claim['status']}")
    return claim


def approve_claim(db: Db, actor: CurrentUser, claim_id: int, body: ClaimReview) -> dict:
    """Approve: mark the fee paid + record a transaction."""
    with db.transaction():
        claim = _lock_pending_claim(db, claim_id)
        fee = db.one("SELECT * FROM fees WHERE id = %s FOR UPDATE", (claim["fee_id"],))
        method = body.method or claim["method"]
        db.execute(
            "UPDATE fee_claims SET status='approved', reviewed_by=%s, reviewed_at=NOW(), "
            "review_note=%s WHERE id=%s",
            (actor.id, body.note, claim_id),
        )
        reference = _mark_paid(db, fee, method) if fee and fee["status"] != "paid" else None
        audit.record(db, actor, "fee_claim.approve", "fee_claim", claim_id,
                     {"fee_id": claim["fee_id"], "method": method, "reference": reference})
        if fee:
            _notify_paid(db, fee)
    return {"approved": claim_id, "reference": reference}


def reject_claim(db: Db, actor: CurrentUser, claim_id: int, body: ClaimReview) -> dict:
    with db.transaction():
        claim = _lock_pending_claim(db, claim_id)
        db.execute(
            "UPDATE fee_claims SET status='rejected', reviewed_by=%s, reviewed_at=NOW(), "
            "review_note=%s WHERE id=%s",
            (actor.id, body.note, claim_id),
        )
        audit.record(db, actor, "fee_claim.reject", "fee_claim", claim_id,
                     {"fee_id": claim["fee_id"], "note": body.note})
        fee = db.one("SELECT title FROM fees WHERE id = %s", (claim["fee_id"],))
        fee_title = fee["title"] if fee else "fee"
        if body.note:
            title, text = f"Payment proof rejected: {body.note}", fee_title
        else:
            title, text = f"Payment proof rejected: {fee_title}", None
        notify.notify(db, [claim["student_id"]], "fee", title, text, data=notify.route("fee", student=True))
    return {"rejected": claim_id}
