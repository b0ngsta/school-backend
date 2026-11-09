"""Fee claim forms (migration 0020) — parents send a payment claim with one
file; the class teacher of the child's section or a manager accepts / declines.
Gated by the school's `fees` feature. The older per-fee payment proofs
(/fees/{id}/claims, /fees/claims…) are separate and unchanged.

Parents (student login, "me"-scoped):
- POST /student/fee-claims                 multipart: amount, paid_on, method, purpose?, fee_id?,
                                           reference_no?, note?, file (PDF / JPEG / PNG / WebP, 15 MB)
- GET  /student/fee-claims                 own forms, newest first (paged)
- GET  /student/fee-claims/{id}
- POST /student/fee-claims/{id}/withdraw   only while pending

Reviewers (class teacher of the form's section, or MANAGERS; drivers / subject teachers never):
- GET  /fee-claim-forms?status_filter=&section_id=   pending first (paged)
- GET  /fee-claim-forms/stats              {pending, accepted, declined, total, can_review, sections}
- GET  /fee-claim-forms/{id}
- PUT  /fee-claim-forms/{id}/accept        {note?, mark_fee_paid?: true}
- PUT  /fee-claim-forms/{id}/decline       {note?}
"""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_feature, require_roles
from app.models.auth import CurrentUser
from app.models.common import PaymentMethod
from app.models.fee_claim_forms import FeeClaimFormAccept, FeeClaimFormDecline
from app.services import fee_claim_forms as service
from app.services.pagination import Page

router = APIRouter(tags=["fee claim forms"], dependencies=[Depends(require_feature("fees"))])

parent = require_roles("student")
reviewer = require_roles(*STAFF)  # managers + teachers; the service narrows teachers to their class-teacher sections


# ---------------------------------------------------------------- parents

@router.post("/student/fee-claims", status_code=201)
def submit_form(
    file: UploadFile = File(...),
    amount: float = Form(...),
    paid_on: date = Form(...),
    method: PaymentMethod = Form("upi"),
    purpose: str | None = Form(None, max_length=150),
    fee_id: int | None = Form(None),
    reference_no: str | None = Form(None, max_length=100),
    note: str | None = Form(None, max_length=500),
    user: CurrentUser = Depends(parent),
    db: Db = Depends(get_db),
):
    return service.submit(db, user, file=file, amount=amount, paid_on=paid_on, method=method, purpose=purpose,
                          fee_id=fee_id, reference_no=reference_no, note=note)


@router.get("/student/fee-claims")
def my_forms(response: Response, page: Page = Depends(page_params), user: CurrentUser = Depends(parent),
             db: Db = Depends(get_db)):
    return paged(response, service.list_mine(db, user, page))


@router.get("/student/fee-claims/{form_id}")
def my_form(form_id: int, user: CurrentUser = Depends(parent), db: Db = Depends(get_db)):
    return service.get_mine(db, user, form_id)


@router.post("/student/fee-claims/{form_id}/withdraw")
def withdraw_form(form_id: int, user: CurrentUser = Depends(parent), db: Db = Depends(get_db)):
    return service.withdraw(db, user, form_id)


# ---------------------------------------------------------------- reviewers

@router.get("/fee-claim-forms")
def list_forms(response: Response, status_filter: str | None = Query(None), section_id: int | None = Query(None),
               page: Page = Depends(page_params), user: CurrentUser = Depends(reviewer), db: Db = Depends(get_db)):
    return paged(response, service.list_staff(db, user, status_filter or None, section_id, page))


@router.get("/fee-claim-forms/stats")
def form_stats(user: CurrentUser = Depends(reviewer), db: Db = Depends(get_db)):
    return service.stats(db, user)


@router.get("/fee-claim-forms/{form_id}")
def get_form(form_id: int, user: CurrentUser = Depends(reviewer), db: Db = Depends(get_db)):
    return service.get_staff(db, user, form_id)


@router.put("/fee-claim-forms/{form_id}/accept")
def accept_form(form_id: int, body: FeeClaimFormAccept, user: CurrentUser = Depends(reviewer),
                db: Db = Depends(get_db)):
    return service.accept(db, user, form_id, body)


@router.put("/fee-claim-forms/{form_id}/decline")
def decline_form(form_id: int, body: FeeClaimFormDecline, user: CurrentUser = Depends(reviewer),
                 db: Db = Depends(get_db)):
    return service.decline(db, user, form_id, body)
