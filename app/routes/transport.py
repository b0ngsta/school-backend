"""Transport — vehicles & routes (managers manage; drivers can view) and the
students who ride each vehicle (stop, pickup / drop time)."""
from fastapi import APIRouter, Depends, Query, Response

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, require_feature, require_roles
from app.models.auth import CurrentUser
from app.models.live_tracking import TrackingMinutes
from app.models.transport import StudentTransportSave, VehicleCreate, VehicleUpdate
from app.services import live_tracking
from app.services import transport as transport_service

router = APIRouter(prefix="/vehicles", tags=["transport"],
                   dependencies=[Depends(require_feature("transport"))])

manager = require_roles(*MANAGERS)
viewer = require_roles(*MANAGERS, "driver")


@router.get("/stats")
def transport_stats(actor: CurrentUser = Depends(viewer), db: Db = Depends(get_db)):
    return transport_service.stats(db)


@router.get("/live")
def live_vehicles(watch: str | None = Query(None, max_length=600,
                                            description="Comma-separated vehicle ids shown on a map now"),
                  actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Every vehicle with Live / Last seen / Inactive and, inside its window, its newest position.
    Vehicles in `watch` make their driver's phone report every 9 s for the next 25 s."""
    ids = set()
    for part in (watch or "").split(","):
        if part.strip().isdigit() and len(ids) < live_tracking.MAX_WATCHED:
            ids.add(int(part))
    return live_tracking.fleet(db, actor.id, ids)


@router.get("")
def list_vehicles(actor: CurrentUser = Depends(viewer), db: Db = Depends(get_db)):
    return transport_service.list_vehicles(db)


@router.post("", status_code=201)
def create_vehicle(body: VehicleCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """`new_driver {username, password, email?}` also creates the driver's sign-in
    (name / phone = driver_name / driver_phone) and links it, in one transaction."""
    return transport_service.create_vehicle(db, body, actor)


@router.put("/{vehicle_id}")
def update_vehicle(vehicle_id: int, body: VehicleUpdate, actor: CurrentUser = Depends(manager),
                   db: Db = Depends(get_db)):
    return transport_service.update_vehicle(db, vehicle_id, body, actor)


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


@router.get("/{vehicle_id}/tracking")
def vehicle_tracking(vehicle_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """When this vehicle's driver shares the bus location: their own minutes around each
    shift (null = the school's), the school's minutes, today's windows and the coming ones."""
    return live_tracking.vehicle_tracking(db, vehicle_id)


@router.put("/{vehicle_id}/tracking")
def set_vehicle_tracking(vehicle_id: int, body: TrackingMinutes, actor: CurrentUser = Depends(manager),
                         db: Db = Depends(get_db)):
    """Set the driver's own minutes before / after each shift (null = the school's default)."""
    return live_tracking.set_vehicle_tracking(db, vehicle_id, body, actor)
