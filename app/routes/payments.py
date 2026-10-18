"""Payment transactions — history + manual entry (managers only)."""
from fastapi import APIRouter, Depends, Response

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.payments import TransactionCreate
from app.services import payments as payment_service
from app.services.pagination import Page

router = APIRouter(prefix="/transactions", tags=["payments"])

manager = require_roles(*MANAGERS)


@router.get("/stats")
def payment_stats(actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return payment_service.stats(db)


@router.get("")
def list_transactions(response: Response, page: Page = Depends(page_params),
                      actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, payment_service.list_transactions(db, page))


@router.post("", status_code=201)
def create_transaction(body: TransactionCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return payment_service.create_transaction(db, actor, body)
