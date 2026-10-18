"""School calendar — named events (holiday / exam / event) with marked dates.

- admin / principal / sub_admin create events and mark their dates
- all staff read via GET /calendar; students via GET /student/calendar
"""
from fastapi import APIRouter, Depends

from app.core.roles import NON_STUDENT
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.calendar import EventCreate, EventUpdate
from app.services import calendar as calendar_service

router = APIRouter(prefix="/calendar", tags=["calendar"])

editor = require_roles("admin", "principal", "sub_admin")
viewer = require_roles(*NON_STUDENT)


@router.get("")
def read_calendar(user: CurrentUser = Depends(viewer), db: Db = Depends(get_db)):
    return calendar_service.get_calendar_payload(db)


@router.post("/events", status_code=201)
def create_event(body: EventCreate, actor: CurrentUser = Depends(editor), db: Db = Depends(get_db)):
    return calendar_service.create_event(db, actor, body)


@router.put("/events/{event_id}")
def update_event(event_id: int, body: EventUpdate, actor: CurrentUser = Depends(editor),
                 db: Db = Depends(get_db)):
    """Update title/category and replace the event's full list of dates."""
    return calendar_service.update_event(db, actor, event_id, body)


@router.delete("/events/{event_id}")
def delete_event(event_id: int, actor: CurrentUser = Depends(editor), db: Db = Depends(get_db)):
    """Dates and synced holiday rows are removed by ON DELETE CASCADE."""
    return calendar_service.delete_event(db, event_id)
