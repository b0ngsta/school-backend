"""Fees — per-student fee records; paying creates a transaction (managers only).

Fee claims: a parent (via the student login) uploads a payment-proof
screenshot; managers/principal/teachers approve or reject. Approving marks
the fee paid and records a transaction.
"""
import secrets
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS
from app.uploads import IMAGE_TYPES, save_upload

router = APIRouter(prefix="/fees", tags=["fees"])

manager = require_roles(*MANAGERS)
claim_reviewer = require_roles(*MANAGERS, "teacher")
student = require_roles("student")

# effective status exposes 'overdue' (pending + past due date) without storing it
EFFECTIVE_STATUS = (
    "CASE WHEN f.status = 'pending' AND f.due_date IS NOT NULL AND f.due_date < CURDATE() "
    "THEN 'overdue' ELSE f.status END"
)


class FeeCreate(BaseModel):
    student_id: int
    title: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0)
    due_date: date | None = None


class FeePay(BaseModel):
    method: Literal["cash", "card", "upi", "netbanking", "wallet"] = "cash"


class FeeGenerate(BaseModel):
    class_id: int
    title: str = Field(min_length=1, max_length=100)
    due_date: date | None = None


@router.get("/stats")
def fee_stats(actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            f"SELECT "
            f"COALESCE(SUM(CASE WHEN f.status='paid' THEN f.amount END), 0) AS collected, "
            f"COALESCE(SUM(CASE WHEN {EFFECTIVE_STATUS}='pending' THEN f.amount END), 0) AS pending, "
            f"COALESCE(SUM(CASE WHEN {EFFECTIVE_STATUS}='overdue' THEN f.amount END), 0) AS overdue, "
            f"COALESCE(SUM(f.amount), 0) AS total "
            f"FROM fees f"
        )
        row = cur.fetchone()
    total = float(row["total"])
    collected = float(row["collected"])
    return {
        "collected": collected,
        "pending": float(row["pending"]),
        "overdue": float(row["overdue"]),
        "total": total,
        "collection_rate": round(collected / total * 100, 1) if total else 0.0,
    }


@router.get("/pending-students")
def pending_students(actor: dict = Depends(manager)):
    """Students with unpaid fees, with pending/overdue totals."""
    with db_cursor() as cur:
        cur.execute(
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
            f"GROUP BY u.id, u.full_name, u.photo_path, sp.roll_no, c.name, s.name "
            f"ORDER BY overdue_total DESC, pending_total DESC"
        )
        return cur.fetchall()


@router.post("/generate", status_code=201)
def generate_class_fees(body: FeeGenerate, actor: dict = Depends(manager)):
    """Create a fee record (class's standard fee_amount) for every student in a class."""
    with db_cursor() as cur:
        cur.execute("SELECT fee_amount FROM classes WHERE id = %s", (body.class_id,))
        cls = cur.fetchone()
        if not cls:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not found")
        if not cls["fee_amount"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This class has no fee amount set — edit the class first",
            )
        cur.execute(
            "SELECT sp.user_id FROM student_profiles sp "
            "JOIN users u ON u.id = sp.user_id "
            "WHERE sp.class_id = %s AND u.is_active = 1",
            (body.class_id,),
        )
        student_ids = [r["user_id"] for r in cur.fetchall()]
        created = 0
        for sid in student_ids:
            cur.execute(
                "SELECT id FROM fees WHERE student_id = %s AND title = %s",
                (sid, body.title),
            )
            if cur.fetchone():
                continue  # already generated
            cur.execute(
                "INSERT INTO fees (student_id, title, amount, due_date) VALUES (%s, %s, %s, %s)",
                (sid, body.title, cls["fee_amount"], body.due_date),
            )
            created += 1
    return {"created": created, "students": len(student_ids)}


@router.get("")
def list_fees(
    status_filter: str | None = None,
    student_id: int | None = None,
    actor: dict = Depends(manager),
):
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
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY f.created_at DESC", tuple(params))
        return cur.fetchall()


@router.post("", status_code=201)
def create_fee(body: FeeCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE id = %s AND user_type = 'student'",
            (body.student_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
        cur.execute(
            "INSERT INTO fees (student_id, title, amount, due_date) VALUES (%s, %s, %s, %s)",
            (body.student_id, body.title, body.amount, body.due_date),
        )
        return {"id": cur.lastrowid}


@router.put("/{fee_id}/pay")
def pay_fee(fee_id: int, body: FeePay, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM fees WHERE id = %s", (fee_id,))
        fee = cur.fetchone()
        if not fee:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee not found")
        if fee["status"] == "paid":
            raise HTTPException(status.HTTP_409_CONFLICT, "Already paid")
        cur.execute(
            "UPDATE fees SET status='paid', paid_date=CURDATE(), method=%s WHERE id=%s",
            (body.method, fee_id),
        )
        reference = f"TXN{secrets.token_hex(5).upper()}"
        cur.execute(
            "INSERT INTO transactions (reference, student_id, fee_id, amount, method, status) "
            "VALUES (%s, %s, %s, %s, %s, 'success')",
            (reference, fee["student_id"], fee_id, fee["amount"], body.method),
        )
    return {"paid": fee_id, "reference": reference}


@router.delete("/{fee_id}")
def delete_fee(fee_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM fees WHERE id = %s", (fee_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee not found")
    return {"deleted": fee_id}


# ---------- fee payment claims (parent-submitted proof) ----------

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


class ClaimReview(BaseModel):
    note: str | None = Field(default=None, max_length=255)
    method: Literal["cash", "card", "upi", "netbanking", "wallet"] | None = None


@router.post("/{fee_id}/claims", status_code=201)
def submit_claim(
    fee_id: int,
    file: UploadFile = File(...),
    method: Literal["cash", "card", "upi", "netbanking", "wallet"] = Form("upi"),
    reference_no: str | None = Form(None),
    note: str | None = Form(None),
    actor: dict = Depends(student),
):
    """Parent (student login) submits a payment-proof screenshot for their fee."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM fees WHERE id = %s AND student_id = %s",
            (fee_id, actor["id"]),
        )
        fee = cur.fetchone()
        if not fee:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee not found")
        if fee["status"] == "paid":
            raise HTTPException(status.HTTP_409_CONFLICT, "This fee is already paid")
        cur.execute(
            "SELECT id FROM fee_claims WHERE fee_id = %s AND status = 'pending'",
            (fee_id,),
        )
        if cur.fetchone():
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A claim for this fee is already under review"
            )
        path = save_upload(file, "claims", IMAGE_TYPES, max_mb=10)
        cur.execute(
            "INSERT INTO fee_claims "
            "(fee_id, student_id, amount, method, reference_no, note, screenshot_path, screenshot_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                fee_id, actor["id"], fee["amount"], method,
                (reference_no or "").strip()[:100] or None,
                (note or "").strip()[:255] or None,
                path, file.filename,
            ),
        )
        return {"id": cur.lastrowid, "status": "pending"}


@router.get("/claims/mine")
def my_claims(actor: dict = Depends(student)):
    with db_cursor() as cur:
        cur.execute(
            CLAIM_SELECT + " WHERE fc.student_id = %s ORDER BY fc.created_at DESC",
            (actor["id"],),
        )
        return cur.fetchall()


@router.get("/claims/stats")
def claim_stats(actor: dict = Depends(claim_reviewer)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(status='pending') AS pending, "
            "SUM(status='approved') AS approved, "
            "SUM(status='rejected') AS rejected "
            "FROM fee_claims"
        )
        row = cur.fetchone()
    return {k: int(v or 0) for k, v in row.items()}


@router.get("/claims")
def list_claims(status_filter: str | None = None, actor: dict = Depends(claim_reviewer)):
    sql = CLAIM_SELECT
    params: tuple = ()
    if status_filter:
        sql += " WHERE fc.status = %s"
        params = (status_filter,)
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY fc.status = 'pending' DESC, fc.created_at DESC", params)
        return cur.fetchall()


@router.put("/claims/{claim_id}/approve")
def approve_claim(claim_id: int, body: ClaimReview, actor: dict = Depends(claim_reviewer)):
    """Approve: mark the fee paid + record a transaction."""
    with db_cursor() as cur:
        cur.execute("SELECT * FROM fee_claims WHERE id = %s", (claim_id,))
        claim = cur.fetchone()
        if not claim:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
        if claim["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Claim already {claim['status']}")
        cur.execute("SELECT * FROM fees WHERE id = %s", (claim["fee_id"],))
        fee = cur.fetchone()
        method = body.method or claim["method"]
        cur.execute(
            "UPDATE fee_claims SET status='approved', reviewed_by=%s, reviewed_at=NOW(), "
            "review_note=%s WHERE id=%s",
            (actor["id"], body.note, claim_id),
        )
        reference = None
        if fee and fee["status"] != "paid":
            cur.execute(
                "UPDATE fees SET status='paid', paid_date=CURDATE(), method=%s WHERE id=%s",
                (method, fee["id"]),
            )
            reference = f"TXN{secrets.token_hex(5).upper()}"
            cur.execute(
                "INSERT INTO transactions (reference, student_id, fee_id, amount, method, status) "
                "VALUES (%s, %s, %s, %s, %s, 'success')",
                (reference, claim["student_id"], fee["id"], fee["amount"], method),
            )
    return {"approved": claim_id, "reference": reference}


@router.put("/claims/{claim_id}/reject")
def reject_claim(claim_id: int, body: ClaimReview, actor: dict = Depends(claim_reviewer)):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM fee_claims WHERE id = %s", (claim_id,))
        claim = cur.fetchone()
        if not claim:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
        if claim["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Claim already {claim['status']}")
        cur.execute(
            "UPDATE fee_claims SET status='rejected', reviewed_by=%s, reviewed_at=NOW(), "
            "review_note=%s WHERE id=%s",
            (actor["id"], body.note, claim_id),
        )
    return {"rejected": claim_id}
