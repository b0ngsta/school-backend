"""School timings: start of day, prayer, period length, periods per day and
breaks. A single row; every timetable (and substitution message) shows
clock times computed from it.

Example: start 06:30 + 30 min prayer -> period 1 at 07:00; a 20 min break
after period 3 shifts period 4 onwards by 20 minutes.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from app.core.clock import SCHOOL_TZ
from app.core.errors import Conflict
from app.db import Db
from app.models.auth import CurrentUser
from app.models.school_timings import SchoolTimings
from app.services import audit

ROW_ID = 1


def _minutes(value) -> int:
    """TIME column (timedelta from PyMySQL), datetime.time or 'HH:MM'."""
    if isinstance(value, timedelta):
        return int(value.total_seconds() // 60)
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    hours, minutes = str(value).split(":")[:2]
    return int(hours) * 60 + int(minutes)


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def compute_periods(start_time, prayer_minutes: int, period_minutes: int, periods_per_day: int,
                    breaks: list[dict]) -> list[dict]:
    at = _minutes(start_time) + prayer_minutes
    after = {int(b["after_period"]): int(b["minutes"]) for b in breaks}
    out = []
    for p in range(1, periods_per_day + 1):
        out.append({"period": p, "start": hm(at), "end": hm(at + period_minutes)})
        at += period_minutes + (after.get(p, 0) if p < periods_per_day else 0)
    return out


def _load(db: Db, lock: bool = False) -> dict | None:
    row = db.one(
        "SELECT t.*, u.full_name AS updated_by_name FROM school_timings t "
        "LEFT JOIN users u ON u.id = t.updated_by ORDER BY t.id LIMIT 1" + (" FOR UPDATE" if lock else "")
    )
    if row and isinstance(row["breaks"], (str, bytes)):
        row["breaks"] = json.loads(row["breaks"])
    return row


def _payload(row: dict) -> dict:
    breaks = sorted(row["breaks"] or [], key=lambda b: b["after_period"])
    return {
        "start_time": hm(_minutes(row["start_time"])),
        "prayer_minutes": row["prayer_minutes"],
        "period_minutes": row["period_minutes"],
        "periods_per_day": row["periods_per_day"],
        "breaks": breaks,
        "periods": compute_periods(row["start_time"], row["prayer_minutes"], row["period_minutes"],
                                   row["periods_per_day"], breaks),
        "updated_at": row["updated_at"],
        "updated_by_name": row["updated_by_name"],
    }


def get_timings(db: Db) -> dict:
    """{} when the timings were never set."""
    row = _load(db)
    return _payload(row) if row else {}


def period_times(db: Db) -> dict[int, dict]:
    """{period: {"start": "07:00", "end": "07:45"}} — empty when timings aren't set."""
    timings = get_timings(db)
    return {p["period"]: p for p in timings.get("periods", [])}


def periods_per_day(db: Db) -> int | None:
    return db.scalar("SELECT periods_per_day FROM school_timings ORDER BY id LIMIT 1")


def period_start(day: date, period: int, times: dict[int, dict]) -> datetime | None:
    """When the period begins in school time (None if timings aren't set)."""
    entry = times.get(period)
    if not entry:
        return None
    hours, minutes = map(int, entry["start"].split(":"))
    return datetime(day.year, day.month, day.day, hours, minutes, tzinfo=SCHOOL_TZ)


def save_timings(db: Db, actor: CurrentUser, body: SchoolTimings, force: bool = False) -> dict:
    breaks = [b.model_dump(exclude_none=True) for b in sorted(body.breaks, key=lambda b: b.after_period)]
    with db.transaction():
        current = _load(db, lock=True)
        beyond = db.all(
            "SELECT DISTINCT u.id, u.full_name FROM timetable_slots t JOIN users u ON u.id = t.teacher_id "
            "WHERE t.period > %s ORDER BY u.full_name",
            (body.periods_per_day,),
        )
        if beyond and not force:
            names = ", ".join(r["full_name"] for r in beyond)
            raise Conflict(
                f"These teachers have classes after period {body.periods_per_day}: {names}. "
                "Move those classes first, or save with ?force=true to delete them."
            )
        if beyond:
            db.execute("DELETE FROM timetable_slots WHERE period > %s", (body.periods_per_day,))
        values = (body.start_time, body.prayer_minutes, body.period_minutes, body.periods_per_day,
                  json.dumps(breaks), actor.id)
        db.execute(
            "INSERT INTO school_timings (id, start_time, prayer_minutes, period_minutes, periods_per_day, "
            "breaks, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE start_time = VALUES(start_time), prayer_minutes = VALUES(prayer_minutes), "
            "period_minutes = VALUES(period_minutes), periods_per_day = VALUES(periods_per_day), "
            "breaks = VALUES(breaks), updated_by = VALUES(updated_by), updated_at = CURRENT_TIMESTAMP",
            (current["id"] if current else ROW_ID, *values),
        )
        audit.record(db, actor, "school_timings.update", "school_timings", ROW_ID,
                     {**body.model_dump(), "deleted_slots_after_period": bool(beyond)})
    return get_timings(db)
