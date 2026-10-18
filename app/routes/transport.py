"""Transport — vehicles & routes (managers manage; drivers can view)."""
from fastapi import APIRouter, Depends

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.transport import VehicleCreate, VehicleUpdate
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
