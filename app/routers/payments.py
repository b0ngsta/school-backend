"""Payment transactions — history + manual entry (managers only).

Real gateway (Razorpay/Stripe) integration is a stub for later: plug it into
POST /transactions and fees.pay_fee.
"""
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS

router = APIRouter(prefix="/transactions", tags=["payments"])

manager = require_roles(*MANAGERS)


class TransactionCreate(BaseModel):
    student_id: int | None = None
    amount: float = Field(gt=0)
    method: Literal["cash", "card", "upi", "netbanking", "wallet"]
    status: Literal["success", "pending", "failed"] = "success"


@router.get("/stats")
def payment_stats(actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN status='success' AND DATE(created_at)=CURDATE() THEN amount END),0) AS today_collection, "
            "COALESCE(SUM(CASE WHEN status='success' AND YEAR(created_at)=YEAR(CURDATE()) AND MONTH(created_at)=MONTH(CURDATE()) THEN amount END),0) AS month_collection, "
            "COUNT(*) AS total_transactions, "
            "COALESCE(ROUND(SUM(status='success') / NULLIF(COUNT(*),0) * 100, 1), 0) AS success_rate "
            "FROM transactions"
        )
        row = cur.fetchone()
    return {
        "today_collection": float(row["today_collection"]),
        "month_collection": float(row["month_collection"]),
        "total_transactions": int(row["total_transactions"]),
        "success_rate": float(row["success_rate"]),
    }


@router.get("")
def list_transactions(actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT t.*, u.full_name AS student_name, f.title AS fee_title "
            "FROM transactions t "
            "LEFT JOIN users u ON u.id = t.student_id "
            "LEFT JOIN fees f ON f.id = t.fee_id "
            "ORDER BY t.created_at DESC LIMIT 200"
        )
        return cur.fetchall()


@router.post("", status_code=201)
def create_transaction(body: TransactionCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        if body.student_id is not None:
            cur.execute(
                "SELECT id FROM users WHERE id = %s AND user_type = 'student'",
                (body.student_id,),
            )
            if not cur.fetchone():
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
        reference = f"TXN{secrets.token_hex(5).upper()}"
        cur.execute(
            "INSERT INTO transactions (reference, student_id, amount, method, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (reference, body.student_id, body.amount, body.method, body.status),
        )
        return {"id": cur.lastrowid, "reference": reference}
