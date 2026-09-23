"""Transport — vehicles & routes (managers only)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS

router = APIRouter(prefix="/vehicles", tags=["transport"])

manager = require_roles(*MANAGERS)
viewer = require_roles(*MANAGERS, "driver")  # drivers can see vehicles & routes


class VehicleCreate(BaseModel):
    vehicle_no: str = Field(min_length=1, max_length=30)
    driver_name: str = Field(min_length=1, max_length=100)
    driver_phone: str | None = None
    route_name: str | None = None
    capacity: int | None = Field(default=None, gt=0)
    status: Literal["active", "maintenance"] = "active"


class VehicleUpdate(BaseModel):
    vehicle_no: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    route_name: str | None = None
    capacity: int | None = None
    status: Literal["active", "maintenance"] | None = None


@router.get("/stats")
def transport_stats(actor: dict = Depends(viewer)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total, SUM(status='active') AS active, "
            "COUNT(DISTINCT route_name) AS routes FROM vehicles"
        )
        row = cur.fetchone()
    return {k: int(v or 0) for k, v in row.items()}


@router.get("")
def list_vehicles(actor: dict = Depends(viewer)):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM vehicles ORDER BY vehicle_no")
        return cur.fetchall()


@router.post("", status_code=201)
def create_vehicle(body: VehicleCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT id FROM vehicles WHERE vehicle_no = %s", (body.vehicle_no,))
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Vehicle number already exists")
        cur.execute(
            "INSERT INTO vehicles (vehicle_no, driver_name, driver_phone, route_name, capacity, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                body.vehicle_no, body.driver_name, body.driver_phone,
                body.route_name, body.capacity, body.status,
            ),
        )
        return {"id": cur.lastrowid}


@router.put("/{vehicle_id}")
def update_vehicle(vehicle_id: int, body: VehicleUpdate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM vehicles WHERE id = %s", (vehicle_id,))
        v = cur.fetchone()
        if not v:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehicle not found")
        merged = {**v, **body.model_dump(exclude_unset=True)}
        cur.execute(
            "UPDATE vehicles SET vehicle_no=%s, driver_name=%s, driver_phone=%s, "
            "route_name=%s, capacity=%s, status=%s WHERE id=%s",
            (
                merged["vehicle_no"], merged["driver_name"], merged["driver_phone"],
                merged["route_name"], merged["capacity"], merged["status"], vehicle_id,
            ),
        )
    return {"updated": vehicle_id}


@router.delete("/{vehicle_id}")
def delete_vehicle(vehicle_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("DELETE FROM vehicles WHERE id = %s", (vehicle_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehicle not found")
    return {"deleted": vehicle_id}
