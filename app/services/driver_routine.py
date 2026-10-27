"""Driver "My Routine": a driver's shifts for one day, each with its students
in stop order and everything needed at the door (address, landmark, map pin,
parent / guardian and phone numbers, notes).

Data:
* transport_shifts + transport_shift_students (migration 0012): several
  trips per vehicle, managers set them up (the /vehicles/{id}/shifts
  endpoints below).
* A vehicle with no rows in transport_shifts is still shown as the ONE
  shift it already had (vehicles.shift_*), with the students of
  student_transport — so a school that set up transport before 0012 sees
  its data straight away. Such a shift has `id: null` and `key: "v<id>"`.
* Student details come from the existing tables: student_profiles
  (address, father / mother name, guardian phone, class), users (name,
  photo, own phone), student_transport (stop name when the shift has none).

A driver only ever sees the vehicles whose driver_id is their own account.
The whole day is a fixed number of queries (no query per shift or student).
Phone numbers and addresses are only returned, never logged.
"""
from __future__ import annotations

from datetime import date, time, timedelta

from app.core.clock import school_now
from app.core.errors import BadRequest, NotFound
from app.db import Db
from app.models.driver_routine import ShiftCreate, ShiftStudentsSave, ShiftUpdate
from app.services import times

NOON = time(12, 0)
DEFAULT_WEEKDAYS = "0,1,2,3,4,5"  # Mon–Sat, like the rest of the school week

_VEHICLE_SQL = ("SELECT id, vehicle_no, vehicle_type, route_name, status, capacity, shift_name, shift_start, "
                "shift_end FROM vehicles WHERE org_id = %s")

_STUDENT_COLUMNS = (
    "u.id AS student_id, u.full_name, u.photo_path, u.phone AS student_phone, sp.roll_no, "
    "c.name AS class_name, s.name AS section_name, sp.father_name, sp.mother_name, sp.guardian_phone, "
    "sp.address"
)
_STUDENT_JOINS = (
    "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
    "LEFT JOIN classes c ON c.id = sp.class_id LEFT JOIN sections s ON s.id = sp.section_id "
)


# ------------------------------------------------------------------ shapes

def _vehicle_brief(v: dict) -> dict:
    return {k: v[k] for k in ("id", "vehicle_no", "vehicle_type", "route_name", "status", "capacity")}


def _coord(value) -> float | None:
    return None if value is None else float(value)


def _student(row: dict, position: int) -> dict:
    return {
        "student_id": row["student_id"],
        "full_name": row["full_name"],
        "photo_path": row["photo_path"],
        "class_name": row["class_name"],
        "section_name": row["section_name"],
        "roll_no": row["roll_no"],
        "stop_no": position,
        "stop_time": times.hhmm(row.get("stop_time")),
        "stop_name": row.get("stop_name"),
        "address": row["address"],
        "landmark": row.get("landmark"),
        "latitude": _coord(row.get("latitude")),
        "longitude": _coord(row.get("longitude")),
        "notes": row.get("notes"),
        "father_name": row["father_name"],
        "mother_name": row["mother_name"],
        "guardian_phone": row["guardian_phone"],
        "student_phone": row["student_phone"],
    }


def _shift(*, key: str, shift_id: int | None, name: str, trip_type: str, start, end, weekdays: str,
           is_active: bool, vehicle: dict, students: list[dict]) -> dict:
    return {
        "key": key,
        "id": shift_id,
        "name": name,
        "trip_type": trip_type,
        "start_time": times.hhmm(start),
        "end_time": times.hhmm(end),
        "weekdays": [int(d) for d in weekdays.split(",") if d.strip()],
        "is_active": bool(is_active),
        "vehicle": _vehicle_brief(vehicle),
        "student_count": len(students),
        "students": [_student(r, i + 1) for i, r in enumerate(students)],
    }


def _sort_key(shift: dict) -> tuple:
    return (shift["start_time"] or "99:99", shift["vehicle"]["vehicle_no"], shift["name"])


def _as_time(value) -> time | None:
    if value is None or isinstance(value, time):
        return value
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
        return time(seconds // 3600 % 24, seconds // 60 % 60)
    return None


# --------------------------------------------------------- building shifts

def _shifts_for(db: Db, vehicles: list[dict], *, only_active: bool) -> list[dict]:
    """Every shift of these vehicles with its students (3 queries at most)."""
    if not vehicles:
        return []
    by_id = {v["id"]: v for v in vehicles}
    marks = ", ".join(["%s"] * len(by_id))
    shift_rows = db.all(
        f"SELECT * FROM transport_shifts WHERE org_id = %s AND vehicle_id IN ({marks}) ORDER BY start_time, id",
        (db.org, *by_id),
    )
    # a vehicle with shifts (even switched-off ones) no longer shows its pre-0012 shift
    with_shifts = {r["vehicle_id"] for r in shift_rows}
    if only_active:
        shift_rows = [r for r in shift_rows if r["is_active"]]
    students: dict[int, list[dict]] = {r["id"]: [] for r in shift_rows}
    if shift_rows:
        shift_marks = ", ".join(["%s"] * len(students))
        for row in db.all(
            f"SELECT tss.shift_id, tss.stop_order, tss.stop_time, COALESCE(tss.stop_name, st.stop_name) AS stop_name, "
            f"tss.landmark, tss.latitude, tss.longitude, tss.notes, {_STUDENT_COLUMNS} "
            "FROM transport_shift_students tss "
            "JOIN users u ON u.id = tss.student_id AND u.org_id = %s AND u.is_active = 1 "
            f"{_STUDENT_JOINS}"
            "LEFT JOIN student_transport st ON st.student_id = u.id AND st.org_id = u.org_id "
            f"WHERE tss.shift_id IN ({shift_marks}) "
            "ORDER BY tss.shift_id, tss.stop_order, tss.stop_time IS NULL, tss.stop_time, u.full_name",
            (db.org, *students),
        ):
            students[row["shift_id"]].append(row)

    out = [
        _shift(key=f"s{r['id']}", shift_id=r["id"], name=r["name"], trip_type=r["trip_type"],
               start=r["start_time"], end=r["end_time"], weekdays=r["weekdays"], is_active=r["is_active"],
               vehicle=by_id[r["vehicle_id"]], students=students[r["id"]])
        for r in shift_rows
    ]

    # vehicles set up before 0012: their one shift + student_transport
    legacy = [v for v in vehicles if v["id"] not in with_shifts]
    if legacy:
        out += _legacy_shifts(db, legacy)
    return sorted(out, key=_sort_key)


def _legacy_shifts(db: Db, vehicles: list[dict]) -> list[dict]:
    marks = ", ".join(["%s"] * len(vehicles))
    rows = db.all(
        f"SELECT st.vehicle_id, st.stop_name, st.pickup_time, st.drop_time, {_STUDENT_COLUMNS} "
        "FROM student_transport st JOIN users u ON u.id = st.student_id AND u.org_id = st.org_id AND u.is_active = 1 "
        f"{_STUDENT_JOINS}"
        f"WHERE st.org_id = %s AND st.vehicle_id IN ({marks})",
        (db.org, *(v["id"] for v in vehicles)),
    )
    out = []
    for v in vehicles:
        riders = [r for r in rows if r["vehicle_id"] == v["id"]]
        if not riders and v["shift_start"] is None:
            continue  # nothing set up for this vehicle yet
        start = _as_time(v["shift_start"])
        trip = "drop" if start is not None and start >= NOON else "pickup"
        for r in riders:
            r["stop_time"] = r["pickup_time"] if trip == "pickup" else r["drop_time"]
        riders.sort(key=lambda r: (r["stop_time"] is None, times.hhmm(r["stop_time"]) or "", r["stop_name"] or "",
                                   r["full_name"]))
        out.append(_shift(key=f"v{v['id']}", shift_id=None, name=v["shift_name"] or "", trip_type=trip,
                          start=v["shift_start"], end=v["shift_end"], weekdays=DEFAULT_WEEKDAYS, is_active=True,
                          vehicle=v, students=riders))
    return out


# ------------------------------------------------------------- the driver

def routine(db: Db, driver_id: int, day: date) -> dict:
    """The driver's shifts on `day` (school-local date)."""
    now = school_now()
    vehicles = db.all(_VEHICLE_SQL + " AND driver_id = %s ORDER BY vehicle_no", (db.org, driver_id))
    holiday = db.one("SELECT name FROM holidays WHERE org_id = %s AND date = %s", (db.org, day))
    weekday = day.weekday()
    shifts = [] if holiday else [
        s for s in _shifts_for(db, vehicles, only_active=True) if weekday in s["weekdays"]
    ]
    return {
        "date": day.isoformat(),
        "weekday": weekday,
        "is_today": day == now.date(),
        "server_time": now.isoformat(timespec="seconds"),
        "holiday": holiday["name"] if holiday else None,
        "vehicles": [_vehicle_brief(v) for v in vehicles],
        "shifts": shifts,
    }


# ------------------------------------------------------------ managers

def _vehicle(db: Db, vehicle_id: int) -> dict:
    v = db.one(_VEHICLE_SQL + " AND id = %s", (db.org, vehicle_id))
    if not v:
        raise NotFound("Vehicle not found")
    return v


def _shift_row(db: Db, vehicle_id: int, shift_id: int, lock: bool = False) -> dict:
    row = db.one(
        "SELECT * FROM transport_shifts WHERE id = %s AND vehicle_id = %s AND org_id = %s"
        + (" FOR UPDATE" if lock else ""),
        (shift_id, vehicle_id, db.org),
    )
    if not row:
        raise NotFound("Shift not found")
    return row


def _check_times(start, end) -> None:
    if end <= start:
        raise BadRequest("The shift must end after it starts")


def list_shifts(db: Db, vehicle_id: int) -> list[dict]:
    """All shifts of a vehicle (active or not) with their students; the
    pre-0012 single shift when the vehicle has none yet."""
    return _shifts_for(db, [_vehicle(db, vehicle_id)], only_active=False)


def create_shift(db: Db, vehicle_id: int, body: ShiftCreate) -> dict:
    _vehicle(db, vehicle_id)
    _check_times(body.start_time, body.end_time)
    shift_id = db.execute(
        "INSERT INTO transport_shifts (org_id, vehicle_id, name, trip_type, start_time, end_time, weekdays, is_active) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (db.org, vehicle_id, body.name.strip(), body.trip_type, body.start_time, body.end_time,
         ",".join(map(str, body.weekdays)), int(body.is_active)),
    ).lastrowid
    return {"id": shift_id}


def update_shift(db: Db, vehicle_id: int, shift_id: int, body: ShiftUpdate) -> dict:
    with db.transaction():
        _vehicle(db, vehicle_id)
        current = _shift_row(db, vehicle_id, shift_id, lock=True)
        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        merged = {**current, **changes}
        _check_times(_as_time(merged["start_time"]), _as_time(merged["end_time"]))
        weekdays = merged["weekdays"]
        db.execute(
            "UPDATE transport_shifts SET name=%s, trip_type=%s, start_time=%s, end_time=%s, weekdays=%s, is_active=%s "
            "WHERE id = %s AND org_id = %s",
            (merged["name"].strip(), merged["trip_type"], _as_time(merged["start_time"]), _as_time(merged["end_time"]),
             weekdays if isinstance(weekdays, str) else ",".join(map(str, weekdays)), int(bool(merged["is_active"])),
             shift_id, db.org),
        )
    return {"updated": shift_id}


def delete_shift(db: Db, vehicle_id: int, shift_id: int) -> None:
    _vehicle(db, vehicle_id)
    _shift_row(db, vehicle_id, shift_id)
    db.execute("DELETE FROM transport_shifts WHERE id = %s AND org_id = %s", (shift_id, db.org))


def save_shift_students(db: Db, vehicle_id: int, shift_id: int, body: ShiftStudentsSave) -> dict:
    """Replace the shift's students; the list order becomes the stop order."""
    ids = [s.student_id for s in body.students]
    if len(set(ids)) != len(ids):
        raise BadRequest("A student is listed twice")
    with db.transaction():
        _vehicle(db, vehicle_id)
        _shift_row(db, vehicle_id, shift_id, lock=True)
        if ids:
            marks = ", ".join(["%s"] * len(ids))
            found = {r["id"] for r in db.all(
                f"SELECT id FROM users WHERE org_id = %s AND user_type = 'student' AND left_on IS NULL "
                f"AND id IN ({marks})",
                (db.org, *ids))}
            if missing := [i for i in ids if i not in found]:
                raise NotFound(f"Student not found: {missing[0]}")
        db.execute("DELETE FROM transport_shift_students WHERE shift_id = %s", (shift_id,))
        db.execute_many(
            "INSERT INTO transport_shift_students (shift_id, student_id, stop_order, stop_time, stop_name, landmark, "
            "latitude, longitude, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(shift_id, s.student_id, i + 1, s.stop_time, (s.stop_name or "").strip() or None,
              (s.landmark or "").strip() or None, s.latitude, s.longitude, (s.notes or "").strip() or None)
             for i, s in enumerate(body.students)],
        )
    return {"shift_id": shift_id, "students": len(ids)}
