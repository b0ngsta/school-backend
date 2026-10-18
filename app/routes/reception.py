"""Reception — admission enquiries (managers only)."""
from fastapi import APIRouter, Depends, Response

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.enquiries import EnquiryCreate, EnquiryUpdate
from app.services import enquiries as enquiry_service
from app.services.pagination import Page

router = APIRouter(prefix="/enquiries", tags=["reception"])

manager = require_roles(*MANAGERS)


@router.get("/stats")
def enquiry_stats(actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return enquiry_service.stats(db)


@router.get("")
def list_enquiries(response: Response, status_filter: str | None = None, page: Page = Depends(page_params),
                   actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return paged(response, enquiry_service.list_enquiries(db, status_filter, page))


@router.post("", status_code=201)
def create_enquiry(body: EnquiryCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return enquiry_service.create_enquiry(db, actor, body)


@router.put("/{enquiry_id}")
def update_enquiry(enquiry_id: int, body: EnquiryUpdate, actor: CurrentUser = Depends(manager),
                   db: Db = Depends(get_db)):
    return enquiry_service.update_enquiry(db, enquiry_id, body)


@router.delete("/{enquiry_id}")
def delete_enquiry(enquiry_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return enquiry_service.delete_enquiry(db, enquiry_id)
