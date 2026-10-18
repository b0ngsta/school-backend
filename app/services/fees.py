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
from app.services import audit, pagination, storage
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
        f"FROM fees f"
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
        f"WHERE f.status = 'pending' "
        f"GROUP BY u.id, u.full_name, u.photo_path, sp.roll_no, c.name, s.name",
        (),
        "overdue_total DESC, pending_total DESC, student_id",
        page,
    )


def generate_class_fees(db: Db, actor: CurrentUser, body: FeeGenerate) -> dict:
    """Create a fee record (class's standard fee_amount) for every active
    student in a class. Re-running with the same title only adds the fee for
    students who don't have it yet."""
    with db.transaction():
        # the row lock serialises concurrent generate calls for the same class
        cls = db.one("SELECT fee_amount FROM classes WHERE id = %s FOR UPDATE", (body.class_id,))
        if not cls:
            raise NotFound("Class not found")
        if not cls["fee_amount"]:
            raise BadRequest("This class has no fee amount set — edit the class first")
        students = db.scalar(
            "SELECT COUNT(*) FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
            "WHERE sp.class_id = %s AND u.is_active = 1",
            (body.class_id,),
        )
        created = db.execute(
            "INSERT INTO fees (student_id, title, amount, due_date) "
            "SELECT sp.user_id, %s, %s, %s FROM student_profiles sp "
            "JOIN users u ON u.id = sp.user_id "
            "WHERE sp.class_id = %s AND u.is_active = 1 "
            "AND NOT EXISTS (SELECT 1 FROM fees f WHERE f.student_id = sp.user_id AND f.title = %s) "
            "ORDER BY sp.user_id",
            (body.title, cls["fee_amount"], body.due_date, body.class_id, body.title),
        ).rowcount
        if created:
            audit.record(db, actor, "fee.generate", "class", body.class_id,
                         {"title": body.title, "created": created})
    return {"created": created, "students": int(students or 0)}


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
    where, params = [], []
    if student_id is not None:
        where.append("f.student_id = %s")
        params.append(student_id)
    if status_filter:
        where.append(f"({EFFECTIVE_STATUS}) = %s")
        params.append(status_filter)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return pagination.fetch(db, sql, params, "f.created_at DESC, f.id DESC", page)


def create_fee(db: Db, actor: CurrentUser, body: FeeCreate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM users WHERE id = %s AND user_type = 'student'", (body.student_id,)):
            raise NotFound("Student not found")
        fee_id = db.execute(
            "INSERT INTO fees (student_id, title, amount, due_date) VALUES (%s, %s, %s, %s)",
            (body.student_id, body.title, body.amount, body.due_date),
        ).lastrowid
        audit.record(db, actor, "fee.create", "fee", fee_id, body.model_dump())
    return {"id": fee_id}


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
        "INSERT INTO transactions (reference, student_id, fee_id, amount, method, status) "
        "VALUES (%s, %s, %s, %s, %s, 'success')",
        (reference, fee["student_id"], fee["id"], fee["amount"], method),
    )
    return reference


def pay_fee(db: Db, actor: CurrentUser, fee_id: int, method: str) -> dict:
    with db.transaction():
        fee = db.one("SELECT * FROM fees WHERE id = %s FOR UPDATE", (fee_id,))
        if not fee:
            raise NotFound("Fee not found")
        reference = _mark_paid(db, fee, method) if fee["status"] != "paid" else None
        if reference is None:
            raise Conflict("Already paid")
        audit.record(db, actor, "fee.pay", "fee", fee_id,
                     {"method": method, "amount": fee["amount"], "reference": reference})
    return {"paid": fee_id, "reference": reference}


def delete_fee(db: Db, actor: CurrentUser, fee_id: int) -> dict:
    with db.transaction():
        fee = db.one("SELECT * FROM fees WHERE id = %s FOR UPDATE", (fee_id,))
        if not fee:
            raise NotFound("Fee not found")
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
        fee = db.one("SELECT * FROM fees WHERE id = %s AND student_id = %s FOR UPDATE",
                     (fee_id, student.id))
        if not fee:
            raise NotFound("Fee not found")
        if fee["status"] == "paid":
            raise Conflict("This fee is already paid")
        if db.one("SELECT id FROM fee_claims WHERE fee_id = %s AND status = 'pending'", (fee_id,)):
            raise Conflict("A claim for this fee is already under review")
        stored = storage.save_upload(db, file, "claims", storage.IMAGE_TYPES, max_mb=10)
        claim_id = db.execute(
            "INSERT INTO fee_claims "
            "(fee_id, student_id, amount, method, reference_no, note, screenshot_path, screenshot_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (fee_id, student.id, fee["amount"], method,
             (reference_no or "").strip()[:100] or None,
             (note or "").strip()[:255] or None,
             stored.path, stored.original_name),
        ).lastrowid
    return {"id": claim_id, "status": "pending"}


def my_claims(db: Db, student: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(db, CLAIM_SELECT + " WHERE fc.student_id = %s", (student.id,),
                            "fc.created_at DESC, fc.id DESC", page)


def claim_stats(db: Db) -> dict:
    row = db.one(
        "SELECT COUNT(*) AS total, "
        "SUM(status='pending') AS pending, "
        "SUM(status='approved') AS approved, "
        "SUM(status='rejected') AS rejected "
        "FROM fee_claims"
    )
    return {k: int(v or 0) for k, v in row.items()}


def list_claims(db: Db, status_filter: str | None, page: pagination.Page | None = None) -> pagination.PageResult:
    sql, params = CLAIM_SELECT, ()
    if status_filter:
        sql += " WHERE fc.status = %s"
        params = (status_filter,)
    return pagination.fetch(db, sql, params, "fc.status = 'pending' DESC, fc.created_at DESC, fc.id DESC", page)


def _lock_pending_claim(db: Db, claim_id: int) -> dict:
    claim = db.one("SELECT * FROM fee_claims WHERE id = %s FOR UPDATE", (claim_id,))
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
    return {"rejected": claim_id}
