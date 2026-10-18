"""Fees — per-student fee records; paying creates a transaction (managers only).

Fee claims: a parent (via the student login) uploads a payment-proof
screenshot; managers/principal/teachers approve or reject. Approving marks
the fee paid and records a transaction.
"""
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.common import PaymentMethod
from app.models.fees import ClaimReview, FeeCreate, FeeGenerate, FeePay
from app.services import fees as fee_service
from app.services.pagination import Page

router = APIRouter(prefix="/fees", tags=["fees"])

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


@router.post("/generate", status_code=201)
def generate_class_fees(body: FeeGenerate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Create a fee record (class's standard fee_amount) for every student in a class."""
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
