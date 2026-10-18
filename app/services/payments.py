"""Payment transactions — history + manual entry.

Real gateway (Razorpay/Stripe) integration is a stub for later: plug it into
create_transaction and fees.pay_fee.
"""
from __future__ import annotations

import secrets

from app.core.errors import NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.payments import TransactionCreate
from app.services import audit, pagination


def new_reference() -> str:
    return f"TXN{secrets.token_hex(5).upper()}"


def stats(db: Db) -> dict:
    row = db.one(
        "SELECT "
        "COALESCE(SUM(CASE WHEN status='success' AND DATE(created_at)=CURDATE() "
        "THEN amount END),0) AS today_collection, "
        "COALESCE(SUM(CASE WHEN status='success' AND YEAR(created_at)=YEAR(CURDATE()) "
        "AND MONTH(created_at)=MONTH(CURDATE()) THEN amount END),0) AS month_collection, "
        "COUNT(*) AS total_transactions, "
        "COALESCE(ROUND(SUM(status='success') / NULLIF(COUNT(*),0) * 100, 1), 0) AS success_rate "
        "FROM transactions"
    )
    return {
        "today_collection": float(row["today_collection"]),
        "month_collection": float(row["month_collection"]),
        "total_transactions": int(row["total_transactions"]),
        "success_rate": float(row["success_rate"]),
    }


def list_transactions(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    """Latest 200 by default (as before); pages reach the full history."""
    return pagination.fetch(
        db,
        "SELECT t.*, u.full_name AS student_name, f.title AS fee_title "
        "FROM transactions t "
        "LEFT JOIN users u ON u.id = t.student_id "
        "LEFT JOIN fees f ON f.id = t.fee_id",
        (),
        "t.created_at DESC, t.id DESC",
        page,
        default_limit=200,
    )


def create_transaction(db: Db, actor: CurrentUser, body: TransactionCreate) -> dict:
    with db.transaction():
        if body.student_id is not None and not db.one(
            "SELECT id FROM users WHERE id = %s AND user_type = 'student'", (body.student_id,)
        ):
            raise NotFound("Student not found")
        reference = new_reference()
        transaction_id = db.execute(
            "INSERT INTO transactions (reference, student_id, amount, method, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (reference, body.student_id, body.amount, body.method, body.status),
        ).lastrowid
        audit.record(db, actor, "transaction.create", "transaction", transaction_id,
                     {"reference": reference, **body.model_dump()})
    return {"id": transaction_id, "reference": reference}
