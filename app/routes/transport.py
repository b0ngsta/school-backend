"""Transport — vehicles & routes (managers manage; drivers can view) and the
students who ride each vehicle (stop, pickup / drop time)."""
from fastapi import APIRouter, Depends, Response

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.transport import StudentTransportSave, VehicleCreate, VehicleUpdate
from app.services import transport as transport_service

router = APIRouter(prefix="/vehicles", tags=["transport"])

manager = require_roles(*MANAGERS)
viewer = require_roles(*MANAGERS, "driver")


@router.get("/stats")
def transport_stats(actor: CurrentUser = Depends(viewer), db: Db = Depends(get_db)):
    return transport_service.stats(db)


@router.get("")
def list_vehicles(actor: CurrentUser = Depends(viewer), db: Db = Depends(get_db)):
    return transport_service.list_vehicles(db)


@router.post("", status_code=201)
def create_vehicle(body: VehicleCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return transport_service.create_vehicle(db, body)


@router.put("/{vehicle_id}")
def update_vehicle(vehicle_id: int, body: VehicleUpdate, actor: CurrentUser = Depends(manager),
                   db: Db = Depends(get_db)):
    return transport_service.update_vehicle(db, vehicle_id, body)


@router.delete("/{vehicle_id}")
def delete_vehicle(vehicle_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return transport_service.delete_vehicle(db, vehicle_id)


@router.get("/{vehicle_id}/students")
def vehicle_students(vehicle_id: int, actor: CurrentUser = Depends(viewer), db: Db = Depends(get_db)):
    return transport_service.vehicle_students(db, vehicle_id)


@router.put("/{vehicle_id}/students/{student_id}")
def assign_student(vehicle_id: int, student_id: int, body: StudentTransportSave,
                   actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Add a student to this vehicle, or change their stop / times (moves them off any other vehicle)."""
    return transport_service.assign_student(db, vehicle_id, student_id, body)


@router.delete("/{vehicle_id}/students/{student_id}", status_code=204)
def remove_student(vehicle_id: int, student_id: int, actor: CurrentUser = Depends(manager),
                   db: Db = Depends(get_db)):
    transport_service.remove_student(db, vehicle_id, student_id)
    return Response(status_code=204)
