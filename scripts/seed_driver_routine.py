"""Demo data for the driver "My Routine" screen (development / testing only).

    python -m scripts.seed_driver_routine [--org DEFAULT] [--driver driver] [--vehicle "DEMO VAN 1"]

Links a van to the driver account and gives it four shifts (Morning 1 / 2
pickup, Evening 1 / 2 drop, Mon–Sat) with the school's existing students
spread over them in stop order (first student gets a map pin, a landmark
and a note). Run it again to rebuild the same demo. It never touches other
vehicles. Needs at least one student in the school.
"""
from __future__ import annotations

import argparse
import sys
from datetime import time
from decimal import Decimal

from app.core.config import settings
from app.db import Db, pool
from app.models.driver_routine import ShiftCreate, ShiftStop, ShiftStudentsSave
from app.services import driver_routine, organisations

SHIFTS = (
    ("Morning Shift 1", "pickup", time(7, 0), time(8, 30)),
    ("Morning Shift 2", "pickup", time(9, 0), time(10, 30)),
    ("Evening Shift 1", "drop", time(13, 30), time(15, 0)),
    ("Evening Shift 2", "drop", time(15, 30), time(17, 0)),
)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--org", default=settings.default_org_code or "DEFAULT")
    p.add_argument("--driver", default="driver", help="the driver's username")
    p.add_argument("--vehicle", default="DEMO VAN 1", help="vehicle number to create / reuse")
    args = p.parse_args(argv)

    db = Db(pool)
    try:
        org = organisations.find(db, args.org)
        if not org:
            sys.exit(f"No school with code {args.org!r}")
        db.bind_org(org["id"])
        driver = db.one("SELECT id, full_name FROM users WHERE org_id = %s AND username = %s AND user_type = 'driver'",
                        (db.org, args.driver))
        if not driver:
            sys.exit(f"No driver account {args.driver!r} in {args.org}")
        students = [r["id"] for r in db.all(
            "SELECT id FROM users WHERE org_id = %s AND user_type = 'student' AND is_active = 1 ORDER BY id LIMIT 100",
            (db.org,))]
        if not students:
            sys.exit("Add some students first (web app or CSV import)")

        with db.transaction():
            vehicle = db.one("SELECT id FROM vehicles WHERE org_id = %s AND vehicle_no = %s", (db.org, args.vehicle))
            if vehicle:
                vehicle_id = vehicle["id"]
                db.execute("UPDATE vehicles SET driver_id = %s WHERE id = %s", (driver["id"], vehicle_id))
                db.execute("DELETE FROM transport_shifts WHERE org_id = %s AND vehicle_id = %s", (db.org, vehicle_id))
            else:
                vehicle_id = db.execute(
                    "INSERT INTO vehicles (org_id, vehicle_no, driver_name, driver_id, vehicle_type, route_name, "
                    "capacity) VALUES (%s, %s, %s, %s, 'van', 'Demo route', 25)",
                    (db.org, args.vehicle, driver["full_name"], driver["id"]),
                ).lastrowid

            half = max(1, (len(students) + 1) // 2)
            groups = (students[:half], students[half:] or students[:half])
            for n, (name, trip, start, end) in enumerate(SHIFTS):
                shift_id = driver_routine.create_shift(
                    db, vehicle_id, ShiftCreate(name=name, trip_type=trip, start_time=start, end_time=end))["id"]
                riders = groups[n % 2][:25]
                stops = []
                for i, student_id in enumerate(riders):
                    minute = start.hour * 60 + start.minute + 5 + i * 3
                    stops.append(ShiftStop(
                        student_id=student_id,
                        stop_time=time(minute // 60 % 24, minute % 60),
                        stop_name=f"Stop {i + 1}",
                        landmark="Opposite the temple gate" if i == 0 else None,
                        latitude=Decimal("26.912434") if i == 0 else None,
                        longitude=Decimal("75.787271") if i == 0 else None,
                        notes="Wait 2 minutes — gate is locked" if i == 0 else None,
                    ))
                driver_routine.save_shift_students(db, vehicle_id, shift_id, ShiftStudentsSave(students=stops))
        print(f"{args.vehicle}: {len(SHIFTS)} shifts for {driver['full_name']} "
              f"({len(students)} students) in {args.org}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
