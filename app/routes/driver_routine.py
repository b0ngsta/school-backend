"""Driver "My Routine" (services/driver_routine.py).

- GET    /driver/routine?date=YYYY-MM-DD                  the driver's shifts that day, students included
- GET    /vehicles/{vehicle_id}/shifts                    managers: every shift of a vehicle + students
- POST   /vehicles/{vehicle_id}/shifts                    managers: add a shift
- PUT    /vehicles/{vehicle_id}/shifts/{shift_id}         managers: change it
- DELETE /vehicles/{vehicle_id}/shifts/{shift_id}         managers: remove it (and its student list)
- PUT    /vehicles/{vehicle_id}/shifts/{shift_id}/students  managers: the shift's students, in stop order
"""
from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.core.clock import school_today
from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.driver_routine import ShiftCreate, ShiftStudentsSave, ShiftUpdate
from app.services import driver_routine as routine_service

router = APIRouter(tags=["transport"])

driver_only = require_roles("driver")
manager = require_roles(*MANAGERS)


@router.get("/driver/routine")
def my_routine(day: date | None = Query(None, alias="date", description="School-local date; default today"),
               actor: CurrentUser = Depends(driver_only), db: Db = Depends(get_db)):
    """`{date, weekday, is_today, server_time, holiday, vehicles, shifts: [{…, students: […]}]}` for the
    signed-in driver's own vehicles only. Shifts are sorted by start time, students by stop order."""
    return routine_service.routine(db, actor.id, day or school_today())


@router.get("/vehicles/{vehicle_id}/shifts")
def list_shifts(vehicle_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return routine_service.list_shifts(db, vehicle_id)


@router.post("/vehicles/{vehicle_id}/shifts", status_code=201)
def create_shift(vehicle_id: int, body: ShiftCreate, actor: CurrentUser = Depends(manager),
                 db: Db = Depends(get_db)):
    return routine_service.create_shift(db, vehicle_id, body)


@router.put("/vehicles/{vehicle_id}/shifts/{shift_id}")
def update_shift(vehicle_id: int, shift_id: int, body: ShiftUpdate, actor: CurrentUser = Depends(manager),
                 db: Db = Depends(get_db)):
    return routine_service.update_shift(db, vehicle_id, shift_id, body)


@router.delete("/vehicles/{vehicle_id}/shifts/{shift_id}", status_code=204)
def delete_shift(vehicle_id: int, shift_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    routine_service.delete_shift(db, vehicle_id, shift_id)
    return Response(status_code=204)


@router.put("/vehicles/{vehicle_id}/shifts/{shift_id}/students")
def save_shift_students(vehicle_id: int, shift_id: int, body: ShiftStudentsSave,
                        actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Replace the shift's students. The list order is the stop order (1, 2, 3 …)."""
    return routine_service.save_shift_students(db, vehicle_id, shift_id, body)
