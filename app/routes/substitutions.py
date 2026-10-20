"""Substitute teachers for a teacher on leave.

- GET  /substitutions/availability   who is free, per date and period the requester teaches
- POST /substitutions                ask a colleague to take one period
- GET  /substitutions/incoming       requests to me · /outgoing  requests I sent
- GET  /substitutions                managers: everything (cover planning)
- PUT  /substitutions/{id}/accept|decline|withdraw   the asked teacher
- PUT  /substitutions/{id}/cancel                    the requester or a manager
"""
from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.substitutions import SubstitutionCreate, SubstitutionResponse
from app.services import substitutions as substitution_service
from app.services.pagination import Page

router = APIRouter(prefix="/substitutions", tags=["substitutions"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)

Status = Query(None, pattern="^(pending|accepted|declined|cancelled|withdrawn|filled)$")


@router.get("/availability")
def availability(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    leave_id: int | None = None,
    teacher_id: int | None = Query(None, description="Managers: plan for this teacher"),
    user: CurrentUser = Depends(staff),
    db: Db = Depends(get_db),
):
    return substitution_service.availability(db, user, from_date, to_date, leave_id, teacher_id)


@router.post("", status_code=201)
def create_request(body: SubstitutionCreate, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return substitution_service.create_request(db, user, body)


@router.get("/incoming")
def incoming(response: Response, status: str | None = Status, from_date: date | None = Query(None, alias="from"),
             to_date: date | None = Query(None, alias="to"), page: Page = Depends(page_params),
             user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return paged(response, substitution_service.incoming(db, user, status, from_date, to_date, page))


@router.get("/outgoing")
def outgoing(response: Response, status: str | None = Status, from_date: date | None = Query(None, alias="from"),
             to_date: date | None = Query(None, alias="to"), page: Page = Depends(page_params),
             user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return paged(response, substitution_service.outgoing(db, user, status, from_date, to_date, page))


@router.get("")
def list_all(response: Response, status: str | None = Status, from_date: date | None = Query(None, alias="from"),
             to_date: date | None = Query(None, alias="to"), day: date | None = Query(None, alias="date"),
             teacher_id: int | None = None, page: Page = Depends(page_params),
             user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, substitution_service.list_all(db, status, from_date, to_date, day, teacher_id, page))


@router.get("/{request_id}")
def get_request(request_id: int, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return substitution_service.get_one(db, user, request_id)


@router.put("/{request_id}/accept")
def accept(request_id: int, body: SubstitutionResponse | None = None, user: CurrentUser = Depends(staff),
           db: Db = Depends(get_db)):
    return substitution_service.accept(db, user, request_id, body.note if body else None)


@router.put("/{request_id}/decline")
def decline(request_id: int, body: SubstitutionResponse | None = None, user: CurrentUser = Depends(staff),
            db: Db = Depends(get_db)):
    return substitution_service.decline(db, user, request_id, body.note if body else None)


@router.put("/{request_id}/withdraw")
def withdraw(request_id: int, body: SubstitutionResponse | None = None, user: CurrentUser = Depends(staff),
             db: Db = Depends(get_db)):
    return substitution_service.withdraw(db, user, request_id, body.note if body else None)


@router.put("/{request_id}/cancel")
def cancel(request_id: int, body: SubstitutionResponse | None = None, user: CurrentUser = Depends(staff),
           db: Db = Depends(get_db)):
    return substitution_service.cancel(db, user, request_id, body.note if body else None)
