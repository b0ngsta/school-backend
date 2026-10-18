"""Transport — vehicles & routes."""
from __future__ import annotations

from app.core.errors import Conflict, DuplicateKey, NotFound
from app.db import Db
from app.models.transport import VehicleCreate, VehicleUpdate


def stats(db: Db) -> dict:
    row = db.one(
        "SELECT COUNT(*) AS total, SUM(status='active') AS active, "
        "COUNT(DISTINCT route_name) AS routes FROM vehicles"
    )
    return {k: int(v or 0) for k, v in row.items()}


def list_vehicles(db: Db) -> list[dict]:
    return db.all("SELECT * FROM vehicles ORDER BY vehicle_no")


def create_vehicle(db: Db, body: VehicleCreate) -> dict:
    if db.one("SELECT id FROM vehicles WHERE vehicle_no = %s", (body.vehicle_no,)):
        raise Conflict("Vehicle number already exists")
    try:
        vehicle_id = db.execute(
            "INSERT INTO vehicles (vehicle_no, driver_name, driver_phone, route_name, capacity, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (body.vehicle_no, body.driver_name, body.driver_phone,
             body.route_name, body.capacity, body.status),
        ).lastrowid
    except DuplicateKey:
        raise Conflict("Vehicle number already exists") from None
    return {"id": vehicle_id}


def update_vehicle(db: Db, vehicle_id: int, body: VehicleUpdate) -> dict:
    with db.transaction():
        current = db.one("SELECT * FROM vehicles WHERE id = %s FOR UPDATE", (vehicle_id,))
        if not current:
            raise NotFound("Vehicle not found")
        merged = {**current, **body.model_dump(exclude_unset=True)}
        try:
            db.execute(
                "UPDATE vehicles SET vehicle_no=%s, driver_name=%s, driver_phone=%s, "
                "route_name=%s, capacity=%s, status=%s WHERE id=%s",
                (merged["vehicle_no"], merged["driver_name"], merged["driver_phone"],
                 merged["route_name"], merged["capacity"], merged["status"], vehicle_id),
            )
        except DuplicateKey:
            raise Conflict("Vehicle number already exists") from None
    return {"updated": vehicle_id}


def delete_vehicle(db: Db, vehicle_id: int) -> dict:
    if db.execute("DELETE FROM vehicles WHERE id = %s", (vehicle_id,)).rowcount == 0:
        raise NotFound("Vehicle not found")
    return {"deleted": vehicle_id}
