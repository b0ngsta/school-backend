"""Transport — vehicles (bus / van) & routes, and which students ride which
vehicle (stop, pickup and drop time). There is no live tracking: parents see
the vehicle, the driver and their child's stop and times."""
from __future__ import annotations

from app.core.errors import BadRequest, Conflict, DuplicateKey, NotFound
from app.db import Db
from app.models.transport import StudentTransportSave, VehicleCreate, VehicleUpdate
from app.services import times

_VEHICLE_COLUMNS = ("vehicle_no", "driver_name", "driver_phone", "route_name", "capacity", "status",
                    "vehicle_type", "driver_id", "shift_name", "shift_start", "shift_end")


def _shape(row: dict | None) -> dict | None:
    return times.format_row(row, "shift_start", "shift_end", "pickup_time", "drop_time")


def stats(db: Db) -> dict:
    row = db.one(
        "SELECT COUNT(*) AS total, SUM(status='active') AS active, "
        "COUNT(DISTINCT route_name) AS routes FROM vehicles WHERE org_id = %s",
        (db.org,),
    )
    return {k: int(v or 0) for k, v in row.items()}


def list_vehicles(db: Db) -> list[dict]:
    rows = db.all(
        "SELECT v.*, d.full_name AS driver_account_name, "
        "(SELECT COUNT(*) FROM student_transport st WHERE st.vehicle_id = v.id) AS student_count "
        "FROM vehicles v LEFT JOIN users d ON d.id = v.driver_id WHERE v.org_id = %s ORDER BY v.vehicle_no",
        (db.org,),
    )
    return [_shape(r) for r in rows]


def _check_driver(db: Db, driver_id: int | None) -> None:
    if driver_id is not None and not db.one(
            "SELECT id FROM users WHERE id = %s AND org_id = %s AND user_type = 'driver'", (driver_id, db.org)):
        raise NotFound("Driver account not found")


def _check_shift(start, end) -> None:
    if start is not None and end is not None and end <= start:
        raise BadRequest("The shift must end after it starts")


def create_vehicle(db: Db, body: VehicleCreate) -> dict:
    if db.one("SELECT id FROM vehicles WHERE org_id = %s AND vehicle_no = %s", (db.org, body.vehicle_no)):
        raise Conflict("Vehicle number already exists")
    _check_driver(db, body.driver_id)
    _check_shift(body.shift_start, body.shift_end)
    try:
        vehicle_id = db.execute(
            f"INSERT INTO vehicles (org_id, {', '.join(_VEHICLE_COLUMNS)}) "
            f"VALUES (%s, {', '.join(['%s'] * len(_VEHICLE_COLUMNS))})",
            (db.org, *(getattr(body, c) for c in _VEHICLE_COLUMNS)),
        ).lastrowid
    except DuplicateKey:
        raise Conflict("Vehicle number already exists") from None
    return {"id": vehicle_id}


def update_vehicle(db: Db, vehicle_id: int, body: VehicleUpdate) -> dict:
    with db.transaction():
        current = db.one("SELECT * FROM vehicles WHERE id = %s AND org_id = %s FOR UPDATE", (vehicle_id, db.org))
        if not current:
            raise NotFound("Vehicle not found")
        changes = body.model_dump(exclude_unset=True)
        for column in ("vehicle_no", "driver_name", "status", "vehicle_type"):  # NOT NULL columns
            if changes.get(column, "") is None:
                changes.pop(column)
        if "driver_id" in changes:
            _check_driver(db, changes["driver_id"])
        merged = {**current, **changes}
        _check_shift(times.hhmm(merged["shift_start"]), times.hhmm(merged["shift_end"]))
        try:
            db.execute(
                f"UPDATE vehicles SET {', '.join(f'{c}=%s' for c in _VEHICLE_COLUMNS)} WHERE id=%s",
                (*(merged[c] for c in _VEHICLE_COLUMNS), vehicle_id),
            )
        except DuplicateKey:
            raise Conflict("Vehicle number already exists") from None
    return {"updated": vehicle_id}


def delete_vehicle(db: Db, vehicle_id: int) -> dict:
    if db.execute("DELETE FROM vehicles WHERE id = %s AND org_id = %s", (vehicle_id, db.org)).rowcount == 0:
        raise NotFound("Vehicle not found")
    return {"deleted": vehicle_id}


# ------------------------------------------------- students on a vehicle

def _vehicle(db: Db, vehicle_id: int) -> dict:
    row = db.one("SELECT id FROM vehicles WHERE id = %s AND org_id = %s", (vehicle_id, db.org))
    if not row:
        raise NotFound("Vehicle not found")
    return row


def vehicle_students(db: Db, vehicle_id: int) -> list[dict]:
    _vehicle(db, vehicle_id)
    rows = db.all(
        "SELECT st.student_id, u.full_name, sp.roll_no, c.name AS class_name, s.name AS section_name, "
        "st.stop_name, st.pickup_time, st.drop_time FROM student_transport st "
        "JOIN users u ON u.id = st.student_id LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN classes c ON c.id = sp.class_id LEFT JOIN sections s ON s.id = sp.section_id "
        "WHERE st.org_id = %s AND st.vehicle_id = %s ORDER BY st.pickup_time, st.stop_name, u.full_name",
        (db.org, vehicle_id),
    )
    return [_shape(r) for r in rows]


def assign_student(db: Db, vehicle_id: int, student_id: int, body: StudentTransportSave) -> dict:
    """Put a student on this vehicle (moves them off any other one)."""
    with db.transaction():
        _vehicle(db, vehicle_id)
        if not db.one("SELECT id FROM users WHERE id = %s AND org_id = %s AND user_type = 'student'",
                      (student_id, db.org)):
            raise NotFound("Student not found")
        db.execute(
            "INSERT INTO student_transport (student_id, org_id, vehicle_id, stop_name, pickup_time, drop_time) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE vehicle_id = VALUES(vehicle_id), "
            "stop_name = VALUES(stop_name), pickup_time = VALUES(pickup_time), drop_time = VALUES(drop_time)",
            (student_id, db.org, vehicle_id, (body.stop_name or "").strip() or None, body.pickup_time,
             body.drop_time),
        )
    return {"student_id": student_id, "vehicle_id": vehicle_id}


def remove_student(db: Db, vehicle_id: int, student_id: int) -> None:
    if db.execute("DELETE FROM student_transport WHERE org_id = %s AND vehicle_id = %s AND student_id = %s",
                  (db.org, vehicle_id, student_id)).rowcount == 0:
        raise NotFound("Student is not on this vehicle")


def for_student(db: Db, student_id: int) -> dict | None:
    """The parent's view: vehicle, driver and the child's stop and times
    (null when the child doesn't use school transport)."""
    row = db.one(
        "SELECT v.id AS vehicle_id, v.vehicle_no, v.vehicle_type, v.route_name, v.status, v.shift_name, "
        "v.shift_start, v.shift_end, COALESCE(d.full_name, v.driver_name) AS driver_name, "
        "COALESCE(v.driver_phone, d.phone) AS driver_phone, d.photo_path AS driver_photo, "
        "st.stop_name, st.pickup_time, st.drop_time "
        "FROM student_transport st JOIN vehicles v ON v.id = st.vehicle_id AND v.org_id = st.org_id "
        "LEFT JOIN users d ON d.id = v.driver_id "
        "WHERE st.student_id = %s AND st.org_id = %s",
        (student_id, db.org),
    )
    return _shape(row)
