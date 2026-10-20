"""Weekly timetable — managers assign; teachers view their own.
day: 0=Mon … 6=Sun · period: 1..12"""
from datetime import date

from fastapi import APIRouter, Depends

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.timetable import SlotUpsert
from app.services import timetable as timetable_service

router = APIRouter(prefix="/timetable", tags=["timetable"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)


@router.get("/mine")
def my_timetable(user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return timetable_service.my_timetable(db, user)


@router.get("/week")
def week_timetable(start: date | None = None, teacher_id: int | None = None,
                   user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """The weekly template for the week of `start` (snapped to Monday) plus holidays,
    leave days and substitutions. Managers may pass `teacher_id`."""
    return timetable_service.week(db, user, start, teacher_id)


@router.get("")
def list_timetable(teacher_id: int | None = None, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Managers: any teacher (or all). Teachers: only their own."""
    return timetable_service.list_timetable(db, user, teacher_id)


@router.put("")
def upsert_slot(body: SlotUpsert, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Create or replace the slot at (teacher, day, period)."""
    return timetable_service.upsert_slot(db, body)


@router.delete("/{slot_id}")
def delete_slot(slot_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return timetable_service.delete_slot(db, slot_id)
