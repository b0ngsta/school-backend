"""Fees — per-student fee records; paying creates a transaction (managers only).

Fee claims: a parent (via the student login) uploads a payment-proof
screenshot; managers/principal/teachers approve or reject. Approving marks
the fee paid and records a transaction.
"""
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_feature, require_roles
from app.models.auth import CurrentUser
from app.models.common import PaymentMethod
from app.models.fees import ClaimReview, FeeCreate, FeeGenerate, FeePay, FeeUnpay
from app.services import fees as fee_service
from app.services.pagination import Page

router = APIRouter(prefix="/fees", tags=["fees"],
                   dependencies=[Depends(require_feature("fees"))])

manager = require_roles(*MANAGERS)
claim_reviewer = require_roles(*MANAGERS, "teacher")
student = require_roles("student")


@router.get("/stats")
def fee_stats(actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return fee_service.stats(db)


@router.get("/pending-students")
def pending_students(response: Response, page: Page = Depends(page_params),
                     actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Students with unpaid fees, with pending/overdue totals."""
    return paged(response, fee_service.pending_students(db, page))


@router.get("/overdue-students")
def overdue_students(response: Response, page: Page = Depends(page_params),
                     actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Students with at least one fee past its due date (largest overdue amount first)."""
    return paged(response, fee_service.overdue_students(db, page))


@router.get("/students/{student_id}/history")
def student_fee_history(student_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """One student's fees (paid and unpaid) and payments, newest first, with totals."""
    return fee_service.student_history(db, student_id)


@router.get("/class-plan")
def class_fee_plan(class_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """The class's fee for this session and the parts (instalments) billed so far."""
    return fee_service.class_plan(db, class_id)


@router.delete("/installments/{installment_id}")
def delete_installment(installment_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Take back a part billed by mistake (only while nobody has paid it)."""
    return fee_service.delete_installment(db, actor, installment_id)


@router.post("/generate", status_code=201)
def generate_class_fees(body: FeeGenerate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Bill every student of a class: `amount` = this part of the class's fee
    (the parts of a session add up to at most the class's fee_amount)."""
    return fee_service.generate_class_fees(db, actor, body)


@router.get("")
def list_fees(
    response: Response,
    status_filter: str | None = None,
    student_id: int | None = None,
    page: Page = Depends(page_params),
    actor: CurrentUser = Depends(manager),
    db: Db = Depends(get_db),
):
    return paged(response, fee_service.list_fees(db, status_filter, student_id, page))


@router.post("", status_code=201)
def create_fee(body: FeeCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return fee_service.create_fee(db, actor, body)


@router.put("/{fee_id}/pay")
def pay_fee(fee_id: int, body: FeePay, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return fee_service.pay_fee(db, actor, fee_id, body.method)


@router.put("/{fee_id}/unpay")
def unpay_fee(fee_id: int, body: FeeUnpay | None = None, actor: CurrentUser = Depends(manager),
              db: Db = Depends(get_db)):
    """Undo "paid": the fee is unpaid again and its payment is kept, marked reversed."""
    return fee_service.unpay_fee(db, actor, fee_id, body.reason if body else None)


@router.delete("/{fee_id}")
def delete_fee(fee_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return fee_service.delete_fee(db, actor, fee_id)


# ---------- fee payment claims (parent-submitted proof) ----------

@router.post("/{fee_id}/claims", status_code=201)
def submit_claim(
    fee_id: int,
    file: UploadFile = File(...),
    method: PaymentMethod = Form("upi"),
    reference_no: str | None = Form(None),
    note: str | None = Form(None),
    actor: CurrentUser = Depends(student),
    db: Db = Depends(get_db),
):
    """Parent (student login) submits a payment-proof screenshot for their fee."""
    return fee_service.submit_claim(db, actor, fee_id, file, method, reference_no, note)


@router.get("/claims/mine")
def my_claims(response: Response, page: Page = Depends(page_params),
              actor: CurrentUser = Depends(student), db: Db = Depends(get_db)):
    return paged(response, fee_service.my_claims(db, actor, page))


@router.get("/claims/stats")
def claim_stats(actor: CurrentUser = Depends(claim_reviewer), db: Db = Depends(get_db)):
    return fee_service.claim_stats(db)


@router.get("/claims")
def list_claims(response: Response, status_filter: str | None = None, page: Page = Depends(page_params),
                actor: CurrentUser = Depends(claim_reviewer), db: Db = Depends(get_db)):
    return paged(response, fee_service.list_claims(db, status_filter, page))


@router.put("/claims/{claim_id}/approve")
def approve_claim(claim_id: int, body: ClaimReview, actor: CurrentUser = Depends(claim_reviewer),
                  db: Db = Depends(get_db)):
    """Approve: mark the fee paid + record a transaction."""
    return fee_service.approve_claim(db, actor, claim_id, body)


@router.put("/claims/{claim_id}/reject")
def reject_claim(claim_id: int, body: ClaimReview, actor: CurrentUser = Depends(claim_reviewer),
                 db: Db = Depends(get_db)):
    return fee_service.reject_claim(db, actor, claim_id, body)
