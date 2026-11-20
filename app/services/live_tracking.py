"""Live bus tracking: the driver's phone is the GPS (migration 0023).

When buses are tracked
    Only inside the school's two windows, on school days (not Sundays, not
    holidays): from `tracking_before_minutes` before the school day starts
    until it starts, and from its end until `tracking_after_minutes` after
    (school_timings; 120 minutes each unless the school set them). A school
    that never saved its timings gets 06:00-08:00 and 14:00-16:00. Outside the
    windows a driver's upload is dropped and a parent gets no position at all.

Driver (POST /driver/location)
    The phone takes a fix about every 9 s and sends them together about every
    50 s. The newest fix replaces the vehicle's ONE row in vehicle_locations;
    about once a minute a fix is also kept in vehicle_location_history (7 days,
    services/retention.py). The answer says how soon to send again:
    {interval_sec, watched, window}.

Parents (GET /student/transport/live)
    Only the vehicle the child is on (student_transport). Every poll pushes the
    vehicle's `watched_until` ~25 s ahead; while it is in the future the driver
    is told to send every 9 s. Nobody has to say "stop": when the polls end,
    the mark runs out by itself. When a parent starts watching a vehicle nobody
    was watching, the driver's phone also gets a silent push so it switches to
    the fast rate at once.

Cost
    A parent poll is normally no query at all here (the child's vehicle, the
    vehicle's row and the school's windows are remembered for a few seconds
    per process), and a write at most every few seconds per vehicle.

Coordinates are never logged. All timestamps in these tables are naive UTC.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time as clock
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.core.clock import SCHOOL_TZ, school_now, utcnow
from app.core.errors import BadRequest, NotFound, TooManyRequests, Unauthorized
from app.db import Db, pool
from app.models.live_tracking import LocationBatch, TrackingMinutes
from app.services import audit, features, notify, school_timings

log = logging.getLogger(__name__)

# ---- what the phones are told (seconds)
SAMPLE_SEC = 9              # a GPS fix this often while moving
UPLOAD_SEC = 50             # ...sent together this often
WATCHED_UPLOAD_SEC = 9      # while a parent is watching
PARKED_SAMPLE_SEC = 30      # not moving
PARKED_UPLOAD_SEC = 120
PARENT_POLL_SEC = 9
OUTSIDE_RECHECK_SEC = 900   # the longest a phone waits before asking again outside a window

WATCH_SECONDS = 25          # one parent poll keeps the vehicle "watched" this long
LIVE_SECONDS_WATCHED = 90   # newer than this = live, once the fast rate is running
LIVE_SECONDS_BASELINE = 120
BOOST_SETTLE_SECONDS = 60   # how long after watching starts the baseline limit still applies
HISTORY_EVERY_SECONDS = 45  # at most one history point per vehicle in this time
HISTORY_DAYS = 7

MAX_POINT_AGE = timedelta(minutes=5)
MAX_CLOCK_AHEAD = timedelta(seconds=60)
MOVING_SPEED = 1.0          # m/s

DEFAULT_MINUTES = 120
MAX_MINUTES = 240
FALLBACK_WINDOWS = ((6 * 60, 8 * 60), (14 * 60, 16 * 60))   # a school without saved timings
SUNDAY = 6

TOKEN_PREFIX = "tk1."
FEATURE = "transport"

# ---- small per-process memories (a dict assignment is atomic under the GIL)
_SCHEDULE_TTL = 60.0
_VEHICLE_TTL = 15.0
_LOCATION_TTL = 4.0
_WATCH_WRITE_EVERY = 5.0
_WATCHER_FRESH = 15.0

_schedules: dict[int, tuple[float, date, Schedule]] = {}
_driver_vehicles: dict[int, tuple[float, list[dict]]] = {}
_student_vehicle: dict[int, tuple[float, dict | None]] = {}
_locations: dict[int, tuple[float, dict | None]] = {}
_plans: dict[int, tuple[float, int, DriverPlan]] = {}  # driver_id -> (when, org_id, plan)
_watch_written: dict[int, float] = {}
_watchers: dict[int, dict[int, float]] = {}
_uploads: dict[int, list[float]] = {}
_lock = threading.Lock()

RATE_WINDOW = 60.0
RATE_MAX = 20               # uploads per driver per minute (the fast rate needs 7)


def reset_caches() -> None:
    """Forget everything remembered in this process (tests; a settings change clears its own school)."""
    for memory in (_schedules, _driver_vehicles, _student_vehicle, _locations, _watch_written, _watchers, _uploads,
                   _plans):
        memory.clear()


# ------------------------------------------------------------------ windows

@dataclass(frozen=True)
class Window:
    kind: str     # "morning" (pickup) | "afternoon" (drop)
    start: int    # minutes after midnight, school time
    end: int

    @property
    def trip(self) -> str:
        return "pickup" if self.kind == "morning" else "drop"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "trip": self.trip, "start": school_timings.hm(self.start),
                "end": school_timings.hm(self.end)}


@dataclass(frozen=True)
class Schedule:
    windows: tuple[Window, ...]
    holiday: str | None   # today's holiday, if any


def windows_from(row: dict | None) -> tuple[Window, ...]:
    """The school's two windows from its school_timings row (None = never saved)."""
    if not row:
        (m0, m1), (a0, a1) = FALLBACK_WINDOWS
        return (Window("morning", m0, m1), Window("afternoon", a0, a1))
    breaks = row["breaks"] or []
    periods = school_timings.compute_periods(row["start_time"], row["prayer_minutes"], row["period_minutes"],
                                             row["periods_per_day"], breaks)
    start = school_timings._minutes(row["start_time"])
    end = school_timings._minutes(periods[-1]["end"]) if periods else start
    before = _minutes_setting(row.get("tracking_before_minutes"))
    after = _minutes_setting(row.get("tracking_after_minutes"))
    out = []
    if before:
        out.append(Window("morning", max(0, start - before), start))
    if after:
        out.append(Window("afternoon", end, min(24 * 60 - 1, end + after)))
    return tuple(out)


def _minutes_setting(value) -> int:
    return DEFAULT_MINUTES if value is None else max(0, min(MAX_MINUTES, int(value)))


def _timings_row(db: Db, org_id: int) -> dict | None:
    import json

    row = db.one("SELECT start_time, prayer_minutes, period_minutes, periods_per_day, breaks, "
                 "tracking_before_minutes, tracking_after_minutes FROM school_timings WHERE org_id = %s "
                 "ORDER BY id LIMIT 1", (org_id,))
    if row and isinstance(row["breaks"], (str, bytes)):
        row["breaks"] = json.loads(row["breaks"])
    return row


def schedule(db: Db, org_id: int) -> Schedule:
    """Today's windows and holiday for a school (remembered for a minute)."""
    today = school_now().date()
    hit = _schedules.get(org_id)
    if hit and hit[1] == today and clock.monotonic() - hit[0] < _SCHEDULE_TTL:
        return hit[2]
    holiday = db.one("SELECT name FROM holidays WHERE org_id = %s AND date = %s", (org_id, today))
    result = Schedule(windows_from(_timings_row(db, org_id)), holiday["name"] if holiday else None)
    _schedules[org_id] = (clock.monotonic(), today, result)
    return result


def forget_schedule(org_id: int) -> None:
    """The school changed its timings, holidays, a driver's shifts or minutes."""
    _schedules.pop(org_id, None)
    for driver_id in [d for d, (_, org, _plan) in list(_plans.items()) if org == org_id]:
        _plans.pop(driver_id, None)


# ------------------------------------------- per driver, around each shift (0027)
# A driver with active shifts (transport_shifts of their vehicles) shares the location from
# `before` minutes before each shift until `after` minutes after it; windows that overlap
# merge. `before` / `after` = the driver's own minutes (driver_tracking_settings), else the
# school's default (school_timings.tracking_*_minutes). A driver without shifts keeps the
# school's two windows, exactly as before. Holidays: no window either way.

@dataclass(frozen=True)
class ShiftSpan:
    trip: str                 # pickup | drop
    start: int                # minutes after midnight
    end: int
    weekdays: frozenset[int]  # Mon=0 … Sun=6


@dataclass(frozen=True)
class DriverPlan:
    spans: tuple[ShiftSpan, ...]    # empty = the school's windows
    before: int
    after: int
    own_before: int | None = None
    own_after: int | None = None

    def windows_on(self, weekday: int) -> tuple[Window, ...]:
        raw = sorted(
            (Window("morning" if s.trip == "pickup" else "afternoon", max(0, s.start - self.before),
                    min(24 * 60 - 1, s.end + self.after))
             for s in self.spans if weekday in s.weekdays),
            key=lambda w: (w.start, w.end))
        merged: list[Window] = []
        for w in raw:
            if merged and w.start <= merged[-1].end:
                last = merged[-1]
                merged[-1] = Window(last.kind, last.start, max(last.end, w.end))
            else:
                merged.append(w)
        return tuple(merged)


def _mins(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return int(value.total_seconds()) // 60 % (24 * 60)
    if hasattr(value, "hour"):
        return value.hour * 60 + value.minute
    return school_timings._minutes(str(value)[:5])


def _plans_for(db: Db, pairs: list[tuple[int, int]], timings: dict[int, dict | None]) -> dict[int, DriverPlan]:
    """Plans of (org_id, driver_id) pairs, any schools: 2 queries. `timings` = school_timings rows by org."""
    if not pairs:
        return {}
    ids = sorted({d for _, d in pairs})
    marks = ", ".join(["%s"] * len(ids))
    spans: dict[int, list[ShiftSpan]] = {}
    for r in db.all(
        "SELECT v.driver_id, ts.trip_type, ts.start_time, ts.end_time, ts.weekdays FROM vehicles v "
        "JOIN transport_shifts ts ON ts.vehicle_id = v.id AND ts.org_id = v.org_id "
        f"WHERE v.driver_id IN ({marks}) AND ts.is_active = 1", ids):
        start, end = _mins(r["start_time"]), _mins(r["end_time"])
        if start is None or end is None or end <= start:
            continue
        days = frozenset(int(d) for d in str(r["weekdays"] or "").split(",") if d.strip().isdigit())
        spans.setdefault(r["driver_id"], []).append(ShiftSpan(r["trip_type"], start, end, days))
    own = {r["driver_id"]: r for r in db.all(
        f"SELECT driver_id, before_minutes, after_minutes FROM driver_tracking_settings WHERE driver_id IN ({marks})",
        ids)}
    out = {}
    for org_id, driver_id in pairs:
        row = timings.get(org_id) or {}
        mine = own.get(driver_id) or {}
        ob, oa = mine.get("before_minutes"), mine.get("after_minutes")
        out[driver_id] = DriverPlan(
            spans=tuple(sorted(spans.get(driver_id, ()), key=lambda s: (s.start, s.end))),
            before=_minutes_setting(row.get("tracking_before_minutes")) if ob is None else int(ob),
            after=_minutes_setting(row.get("tracking_after_minutes")) if oa is None else int(oa),
            own_before=ob, own_after=oa)
    return out


def driver_plan(db: Db, org_id: int, driver_id: int | None) -> DriverPlan | None:
    """The driver's plan (remembered for a minute); None for no driver."""
    if not driver_id:
        return None
    hit = _plans.get(driver_id)
    if hit and hit[1] == org_id and clock.monotonic() - hit[0] < _SCHEDULE_TTL:
        return hit[2]
    plan = _plans_for(db, [(org_id, driver_id)], {org_id: _timings_row(db, org_id)})[driver_id]
    _plans[driver_id] = (clock.monotonic(), org_id, plan)
    return plan


def _day_windows(sched: Schedule, plan: DriverPlan | None, day: date) -> tuple[Window, ...]:
    """The windows of one day (holidays are the caller's business)."""
    if plan is not None and plan.spans:
        return plan.windows_on(day.weekday())
    return () if day.weekday() == SUNDAY else sched.windows


def _at(day: date, minutes: int) -> datetime:
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=SCHOOL_TZ)


def current_window(sched: Schedule, now: datetime, plan: DriverPlan | None = None) -> Window | None:
    """The window running at `now` (school time), or None."""
    if sched.holiday:
        return None
    minute = now.hour * 60 + now.minute
    return next((w for w in _day_windows(sched, plan, now.date()) if w.start <= minute < w.end), None)


def upcoming(db: Db, org_id: int, sched: Schedule, now: datetime, days: int = 7,
             plan: DriverPlan | None = None) -> list[dict]:
    """The windows still to come (today's running one included), holidays and
    Sundays (school windows) left out. The driver's phone sets its own alarms from this."""
    last = now.date() + timedelta(days=days - 1)
    holidays = {r["date"] for r in db.all(
        "SELECT date FROM holidays WHERE org_id = %s AND date BETWEEN %s AND %s", (org_id, now.date(), last))}
    out = []
    for offset in range(days):
        day = now.date() + timedelta(days=offset)
        if day in holidays:
            continue
        for w in _day_windows(sched, plan, day):
            start, end = _at(day, w.start), _at(day, w.end)
            if end > now:
                out.append({"kind": w.kind, "trip": w.trip, "start_at": start.isoformat(timespec="seconds"),
                            "end_at": end.isoformat(timespec="seconds")})
    return out


def _window_state(db: Db, org_id: int, now: datetime | None = None,
                  plan: DriverPlan | None = None) -> tuple[Window | None, dict]:
    """(running window, what clients are told about it) for a driver's plan, or the school's windows."""
    now = now or school_now()
    sched = schedule(db, org_id)
    window = current_window(sched, now, plan)
    if window:
        return window, {"active": True, "kind": window.kind, "trip": window.trip,
                        "ends_at": _at(now.date(), window.end).isoformat(timespec="seconds"), "next_start": None}
    nxt = upcoming(db, org_id, sched, now, plan=plan)
    return None, {"active": False, "kind": None, "trip": None, "ends_at": None,
                  "next_start": nxt[0]["start_at"] if nxt else None, "holiday": sched.holiday}


def _iso_utc(value: datetime | None) -> str | None:
    return None if value is None else value.replace(tzinfo=UTC).astimezone(SCHOOL_TZ).isoformat(timespec="seconds")


# ----------------------------------------------------------- driver's vehicle

def _vehicles_of(db: Db, driver_id: int) -> list[dict]:
    hit = _driver_vehicles.get(driver_id)
    if hit and clock.monotonic() - hit[0] < _VEHICLE_TTL:
        return hit[1]
    rows = db.all("SELECT id, vehicle_no, vehicle_type FROM vehicles WHERE org_id = %s AND driver_id = %s ORDER BY id",
                  (db.org, driver_id))
    _driver_vehicles[driver_id] = (clock.monotonic(), rows)
    return rows


def _driver_vehicle(db: Db, driver_id: int, vehicle_id: int | None) -> dict | None:
    vehicles = _vehicles_of(db, driver_id)
    if vehicle_id is None:
        return vehicles[0] if vehicles else None
    match = next((v for v in vehicles if v["id"] == vehicle_id), None)
    if match is None:  # another driver's, another school's or no such vehicle: all the same answer
        raise NotFound("Vehicle not found")
    return match


INTERVALS = {
    "sample_sec": SAMPLE_SEC, "upload_sec": UPLOAD_SEC, "watched_upload_sec": WATCHED_UPLOAD_SEC,
    "parked_sample_sec": PARKED_SAMPLE_SEC, "parked_upload_sec": PARKED_UPLOAD_SEC,
    "parked_speed": MOVING_SPEED, "max_points": 20,
}


def config(db: Db, driver_id: int) -> dict:
    """GET /driver/tracking/config: what the driver's phone needs to run by itself."""
    now = school_now()
    vehicles = _vehicles_of(db, driver_id)
    sched = schedule(db, db.org)
    plan = driver_plan(db, db.org, driver_id)
    window, state = _window_state(db, db.org, now, plan)
    follows = bool(plan and plan.spans)
    return {
        "enabled": bool(vehicles),
        "vehicle": vehicles[0] if vehicles else None,
        "vehicles": vehicles,
        "active_now": bool(window and vehicles),
        "window": state,
        # today's windows: around the driver's shifts when they have some, else the school's
        "windows": [w.as_dict() for w in (_day_windows(sched, plan, now.date()) if follows else sched.windows)],
        "follows_shifts": follows,
        "holiday": sched.holiday,
        "upcoming": upcoming(db, db.org, sched, now, plan=plan) if vehicles else [],
        "intervals": INTERVALS,
        "server_time": now.isoformat(timespec="seconds"),
    }


# -------------------------------------------------------------- upload

def _check_rate(driver_id: int) -> None:
    now = clock.monotonic()
    with _lock:
        recent = [t for t in _uploads.get(driver_id, ()) if now - t < RATE_WINDOW]
        if len(recent) >= RATE_MAX:
            _uploads[driver_id] = recent
            raise TooManyRequests("Too many location uploads. Slow down.",
                                  retry_after_seconds=int(RATE_WINDOW - (now - recent[0])) + 1)
        recent.append(now)
        _uploads[driver_id] = recent


def _naive_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(UTC).replace(tzinfo=None)


def report(db: Db, driver_id: int, body: LocationBatch) -> dict:
    """Store the driver's newest fix. Returns {interval_sec, watched, window, accepted}."""
    _check_rate(driver_id)
    vehicle = _driver_vehicle(db, driver_id, body.vehicle_id)
    window, state = _window_state(db, db.org, plan=driver_plan(db, db.org, driver_id))
    if vehicle is None or window is None:
        wait = OUTSIDE_RECHECK_SEC
        if state.get("next_start"):
            until = (datetime.fromisoformat(state["next_start"]) - school_now()).total_seconds()
            wait = int(max(SAMPLE_SEC, min(OUTSIDE_RECHECK_SEC, until)))
        return {"interval_sec": wait, "watched": False, "window": state, "accepted": 0,
                "vehicle_id": vehicle["id"] if vehicle else None}

    now = utcnow()
    points = sorted(((_naive_utc(p.recorded_at), p) for p in body.points), key=lambda pair: pair[0])
    good = [(at, p) for at, p in points if now - MAX_POINT_AGE <= at <= now + MAX_CLOCK_AHEAD]
    vehicle_id = vehicle["id"]
    row = db.one("SELECT recorded_at, history_at, watched_until FROM vehicle_locations WHERE vehicle_id = %s "
                 "AND org_id = %s", (vehicle_id, db.org))
    watched = bool(row and row["watched_until"] and row["watched_until"] > now)
    if good:
        at, p = good[-1]
        at = min(at, now)
        keep_history = not row or not row["history_at"] or (at - row["history_at"]).total_seconds() >= \
            HISTORY_EVERY_SECONDS
        if not row or not row["recorded_at"] or at > row["recorded_at"]:
            values = (p.latitude, p.longitude, p.speed, p.heading, p.accuracy, at, now, driver_id)
            db.execute(
                "INSERT INTO vehicle_locations (vehicle_id, org_id, latitude, longitude, speed, heading, accuracy, "
                "recorded_at, received_at, reported_by, history_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE latitude = VALUES(latitude), longitude = VALUES(longitude), "
                "speed = VALUES(speed), heading = VALUES(heading), accuracy = VALUES(accuracy), "
                "recorded_at = VALUES(recorded_at), received_at = VALUES(received_at), "
                "reported_by = VALUES(reported_by), history_at = COALESCE(VALUES(history_at), history_at)",
                (vehicle_id, db.org, *values, at if keep_history else None),
            )
            if keep_history:
                db.execute(
                    "INSERT INTO vehicle_location_history (org_id, vehicle_id, latitude, longitude, speed, heading, "
                    "accuracy, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (db.org, vehicle_id, p.latitude, p.longitude, p.speed, p.heading, p.accuracy, at),
                )
            _locations.pop(vehicle_id, None)  # parents in this process see it on their next poll
    return {"interval_sec": WATCHED_UPLOAD_SEC if watched else UPLOAD_SEC, "watched": watched, "window": state,
            "accepted": len(good), "vehicle_id": vehicle_id}


# ----------------------------------------------------------------- parents

def _vehicle_of_student(db: Db, student_id: int) -> dict | None:
    hit = _student_vehicle.get(student_id)
    if hit and clock.monotonic() - hit[0] < _VEHICLE_TTL:
        return hit[1]
    row = db.one(
        "SELECT v.id, v.vehicle_no, v.vehicle_type, v.driver_id FROM student_transport st "
        "JOIN vehicles v ON v.id = st.vehicle_id AND v.org_id = st.org_id "
        "WHERE st.student_id = %s AND st.org_id = %s", (student_id, db.org))
    _student_vehicle[student_id] = (clock.monotonic(), row)
    return row


def _location(db: Db, vehicle_id: int) -> dict | None:
    hit = _locations.get(vehicle_id)
    if hit and clock.monotonic() - hit[0] < _LOCATION_TTL:
        return hit[1]
    row = db.one("SELECT latitude, longitude, speed, heading, accuracy, recorded_at, watch_started_at "
                 "FROM vehicle_locations WHERE vehicle_id = %s AND org_id = %s", (vehicle_id, db.org))
    _locations[vehicle_id] = (clock.monotonic(), row)
    return row


def _watch(db: Db, vehicle: dict, student_id: int) -> None:
    """A parent is looking: keep the vehicle "watched" for the next WATCH_SECONDS."""
    vehicle_id = vehicle["id"]
    mono = clock.monotonic()
    _watchers.setdefault(vehicle_id, {})[student_id] = mono
    if mono - _watch_written.get(vehicle_id, -1e9) < _WATCH_WRITE_EVERY:
        return
    _watch_written[vehicle_id] = mono
    now = utcnow()
    until = now + timedelta(seconds=WATCH_SECONDS)
    # 1 row changed = nobody was watching until now (the driver's phone is still on the slow rate)
    started = db.execute(
        "UPDATE vehicle_locations SET watched_until = %s, watch_started_at = %s WHERE vehicle_id = %s AND org_id = %s "
        "AND (watched_until IS NULL OR watched_until <= %s)", (until, now, vehicle_id, db.org, now)).rowcount
    if not started:
        created = db.execute(
            "INSERT INTO vehicle_locations (vehicle_id, org_id, watched_until, watch_started_at) "
            "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE watched_until = VALUES(watched_until)",
            (vehicle_id, db.org, until, now)).rowcount
        started = created == 1  # 1 = inserted (first ever watch), 2 = extended
    if started:
        _locations.pop(vehicle_id, None)
        if vehicle.get("driver_id"):
            notify.send_data(db, [vehicle["driver_id"]], {"type": "tracking", "action": "boost",
                                                          "vehicle_id": str(vehicle_id)})


def live(db: Db, student_id: int) -> dict:
    """GET /student/transport/live: where the child's bus is, if it may be shown now."""
    now_school = school_now()
    base = {"server_time": now_school.isoformat(timespec="seconds"), "poll_sec": PARENT_POLL_SEC,
            "vehicle": None, "position": None, "age_sec": None, "last_seen_min": None, "moving": None}
    vehicle = _vehicle_of_student(db, student_id)
    window, state = _window_state(db, db.org, now_school,
                                  driver_plan(db, db.org, vehicle["driver_id"]) if vehicle else None)
    base["window"] = state
    if vehicle is None:
        return {**base, "state": "no_vehicle"}
    base["vehicle"] = {"id": vehicle["id"], "vehicle_no": vehicle["vehicle_no"],
                       "vehicle_type": vehicle["vehicle_type"]}
    if window is None:
        return {**base, "state": "outside_window"}

    _watch(db, vehicle, student_id)
    row = _location(db, vehicle["id"])
    now = utcnow()
    window_start = _at(now_school.date(), window.start).astimezone(UTC).replace(tzinfo=None)
    if not row or not row["recorded_at"] or row["recorded_at"] < window_start:
        return {**base, "state": "stale"}  # nothing from the phone in this window: no old position is shown
    age = max(0, int((now - row["recorded_at"]).total_seconds()))
    boosted = bool(row["watch_started_at"]) and (now - row["watch_started_at"]).total_seconds() > BOOST_SETTLE_SECONDS
    limit = LIVE_SECONDS_WATCHED if boosted else LIVE_SECONDS_BASELINE
    speed = None if row["speed"] is None else float(row["speed"])
    return {
        **base,
        "state": "live" if age <= limit else "stale",
        "position": {"latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
                     "heading": None if row["heading"] is None else float(row["heading"]), "speed": speed,
                     "accuracy": None if row["accuracy"] is None else float(row["accuracy"]),
                     "recorded_at": _iso_utc(row["recorded_at"])},
        "age_sec": age,
        "last_seen_min": age // 60,
        "moving": None if speed is None else speed >= MOVING_SPEED,
    }


def stop_watching(db: Db, student_id: int) -> dict:
    """POST /student/transport/live/stop (best effort): the Track screen closed.
    If no other parent polled this vehicle here in the last seconds, the driver
    goes back to the slow rate at once instead of when the mark runs out."""
    vehicle = _vehicle_of_student(db, student_id)
    if vehicle is None:
        return {"stopped": False}
    watchers = _watchers.get(vehicle["id"], {})
    watchers.pop(student_id, None)
    mono = clock.monotonic()
    if any(mono - seen < _WATCHER_FRESH for seen in watchers.values()):
        return {"stopped": False}
    _watch_written.pop(vehicle["id"], None)
    db.execute("UPDATE vehicle_locations SET watched_until = %s WHERE vehicle_id = %s AND org_id = %s "
               "AND watched_until > %s", (utcnow(), vehicle["id"], db.org, utcnow()))
    return {"stopped": True}


# ----------------------------------------------------------------- managers

MANAGER_POLL_SEC = 30        # the Transport list
MAX_WATCHED = 50             # vehicles one map may watch at once


def _plans_cached(db: Db, driver_ids: list[int]) -> dict[int, DriverPlan]:
    """Plans of many drivers of this school: remembered ones first, the rest in one go."""
    mono = clock.monotonic()
    out, misses = {}, []
    for driver_id in set(driver_ids):
        hit = _plans.get(driver_id)
        if hit and hit[1] == db.org and mono - hit[0] < _SCHEDULE_TTL:
            out[driver_id] = hit[2]
        else:
            misses.append(driver_id)
    if misses:
        fresh = _plans_for(db, [(db.org, d) for d in misses], {db.org: _timings_row(db, db.org)})
        for driver_id, plan in fresh.items():
            _plans[driver_id] = (mono, db.org, plan)
        out.update(fresh)
    return out


def fleet(db: Db, viewer_id: int, watch: set[int] | frozenset[int] = frozenset()) -> dict:
    """GET /vehicles/live (managers): every vehicle of the school with its live state.
    `watch` = vehicles shown on a map right now: their drivers switch to the fast rate,
    like a parent watching. A fixed number of queries, whatever the number of vehicles."""
    now_school = school_now()
    now = utcnow()
    vehicles = db.all(
        "SELECT v.id, v.vehicle_no, v.vehicle_type, v.route_name, v.status, v.driver_id, v.driver_name, "
        "u.full_name AS driver_account_name FROM vehicles v "
        "LEFT JOIN users u ON u.id = v.driver_id AND u.org_id = v.org_id AND u.is_active = 1 "
        "WHERE v.org_id = %s ORDER BY v.vehicle_no, v.id", (db.org,))
    sched = schedule(db, db.org)
    plans = _plans_cached(db, [v["driver_id"] for v in vehicles if v["driver_id"] and v["driver_account_name"]])
    locations = {r["vehicle_id"]: r for r in db.all(
        "SELECT vehicle_id, latitude, longitude, speed, heading, accuracy, recorded_at, watch_started_at "
        "FROM vehicle_locations WHERE org_id = %s", (db.org,))} if vehicles else {}
    today = now_school.date()
    minute = now_school.hour * 60 + now_school.minute
    out = []
    for v in vehicles:
        signed_in = bool(v["driver_id"] and v["driver_account_name"])
        plan = plans.get(v["driver_id"]) if signed_in else None
        windows = () if sched.holiday or not signed_in else _day_windows(sched, plan, today)
        window = next((w for w in windows if w.start <= minute < w.end), None)
        row = locations.get(v["id"])
        item = {
            "id": v["id"], "vehicle_no": v["vehicle_no"], "vehicle_type": v["vehicle_type"],
            "route_name": v["route_name"], "status": v["status"],
            "driver_name": v["driver_account_name"] or v["driver_name"],
            "driver_id": v["driver_id"] if signed_in else None,
            "follows_shifts": bool(plan and plan.spans),
            "windows": [w.as_dict() for w in windows],
            "window": None if window is None else {
                **window.as_dict(), "ends_at": _at(today, window.end).isoformat(timespec="seconds")},
            "last_seen_at": _iso_utc(row["recorded_at"]) if row and row["recorded_at"] else None,
            "state": "inactive", "reason": None, "position": None, "age_sec": None, "last_seen_min": None,
            "moving": None,
        }
        if not signed_in:
            item["reason"] = "no_sign_in"
        elif sched.holiday:
            item["reason"] = "holiday"
        elif window is None:
            item["reason"] = "outside_window"
        else:
            window_start = _at(today, window.start).astimezone(UTC).replace(tzinfo=None)
            if v["id"] in watch:
                _watch(db, v, -viewer_id)   # staff ids never clash with a student's watch
            if not row or not row["recorded_at"] or row["recorded_at"] < window_start:
                item["reason"] = "no_data"   # nothing from the phone in this window yet
            else:
                age = max(0, int((now - row["recorded_at"]).total_seconds()))
                boosted = bool(row["watch_started_at"]) and \
                    (now - row["watch_started_at"]).total_seconds() > BOOST_SETTLE_SECONDS
                speed = None if row["speed"] is None else float(row["speed"])
                item.update({
                    "state": "live" if age <= (LIVE_SECONDS_WATCHED if boosted else LIVE_SECONDS_BASELINE)
                    else "stale",
                    "position": {"latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
                                 "heading": None if row["heading"] is None else float(row["heading"]),
                                 "speed": speed,
                                 "accuracy": None if row["accuracy"] is None else float(row["accuracy"]),
                                 "recorded_at": _iso_utc(row["recorded_at"])},
                    "age_sec": age, "last_seen_min": age // 60,
                    "moving": None if speed is None else speed >= MOVING_SPEED,
                })
        out.append(item)
    return {"server_time": now_school.isoformat(timespec="seconds"), "holiday": sched.holiday,
            "poll_sec": PARENT_POLL_SEC if watch else MANAGER_POLL_SEC,
            "live": sum(1 for i in out if i["state"] == "live"), "vehicles": out}


# --------------------------------------------------------- tracking tokens

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Db, driver_id: int, session_id: str) -> dict:
    """A key for the phone's location service: it can only send this driver's
    location, and stops working when this sign-in ends. One per sign-in."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO driver_tracking_tokens (org_id, user_id, session_id, token_hash, created_at) "
        "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE token_hash = VALUES(token_hash), "
        "created_at = VALUES(created_at), last_used_at = NULL",
        (db.org, driver_id, session_id, _hash(token), utcnow()))
    return {"token": token}


@dataclass(frozen=True)
class TokenDriver:
    id: int
    org_id: int


_token_touch: dict[str, float] = {}


def driver_from_token(db: Db, token: str) -> TokenDriver:
    """Who a tracking token belongs to; binds `db` to their school. 401 unless
    the account is an active driver of an active school, still signed in on
    that phone, with transport switched on."""
    digest = _hash(token)
    row = db.one(
        "SELECT t.user_id, u.org_id, u.is_active, u.user_type, o.is_active AS org_active, o.features_version, "
        "s.revoked_at, s.id AS session_id FROM driver_tracking_tokens t "
        "JOIN users u ON u.id = t.user_id JOIN organisations o ON o.id = u.org_id "
        "LEFT JOIN auth_sessions s ON s.id = t.session_id AND s.user_id = u.id WHERE t.token_hash = %s", (digest,))
    if (not row or not row["is_active"] or not row["org_active"] or row["user_type"] != "driver"
            or row["session_id"] is None or row["revoked_at"] is not None):
        raise Unauthorized("Invalid or expired token")
    if not features.for_org(db, row["org_id"], int(row["features_version"] or 0)).get(FEATURE, True):
        from app.core.errors import FeatureDisabled
        raise FeatureDisabled(FEATURE)
    db.bind_org(row["org_id"])
    mono = clock.monotonic()
    if mono - _token_touch.get(digest, -1e9) > 3600:  # "last used" to the hour is plenty
        _token_touch[digest] = mono
        db.execute("UPDATE driver_tracking_tokens SET last_used_at = %s WHERE token_hash = %s", (utcnow(), digest))
    return TokenDriver(id=row["user_id"], org_id=row["org_id"])


# ------------------------------------------- window start: wake the phones

_TICK_EVERY = 60.0
_START_GRACE_MINUTES = 5
_next_tick = 0.0


def tick_if_due() -> int | None:
    """Called by the background worker's loop; at most once a minute."""
    global _next_tick
    if clock.monotonic() < _next_tick:
        return None
    _next_tick = clock.monotonic() + _TICK_EVERY
    db = Db(pool)
    try:
        return announce_window_starts(db)
    finally:
        db.close()


def announce_window_starts(db: Db, now: datetime | None = None) -> int:
    """Send a silent "start sharing" push to the drivers of every school whose
    window began in the last minutes. Once per school, day and window, whichever
    process gets there first (scheduled_runs). The phones also set their own
    alarm, so a late or lost push only delays the start. Returns drivers told.

    Runs across schools (no db.org): a fixed number of queries, never one per school."""
    if not notify.push_configured():
        return 0
    import json

    now = now or school_now()
    today, minute = now.date(), now.hour * 60 + now.minute
    drivers: dict[int, list[int]] = {}
    for r in db.all("SELECT DISTINCT v.org_id, v.driver_id FROM vehicles v JOIN users u ON u.id = v.driver_id "
                    "JOIN organisations o ON o.id = v.org_id WHERE u.is_active = 1 AND o.is_active = 1"):
        drivers.setdefault(r["org_id"], []).append(r["driver_id"])
    if not drivers:
        return 0
    marks = ", ".join(["%s"] * len(drivers))
    timings = {}
    for r in db.all("SELECT org_id, start_time, prayer_minutes, period_minutes, periods_per_day, breaks, "
                    f"tracking_before_minutes, tracking_after_minutes FROM school_timings WHERE org_id IN ({marks})",
                    tuple(drivers)):
        if isinstance(r["breaks"], (str, bytes)):
            r["breaks"] = json.loads(r["breaks"])
        timings.setdefault(r["org_id"], r)
    closed = {r["org_id"] for r in db.all(
        f"SELECT org_id FROM holidays WHERE date = %s AND org_id IN ({marks})", (today, *drivers))}
    open_orgs = {org_id: ids for org_id, ids in drivers.items()
                 if org_id not in closed and features.for_org(db, org_id).get(FEATURE, True)}
    plans = _plans_for(db, [(org_id, d) for org_id, ids in open_orgs.items() for d in ids], timings)

    def starting(w: Window) -> bool:
        return w.start <= minute < min(w.end, w.start + _START_GRACE_MINUTES)

    def once(name: str) -> bool:
        return bool(db.execute("INSERT IGNORE INTO scheduled_runs (name, last_run_at) VALUES (%s, %s)",
                               (name, utcnow())).rowcount)

    told = 0
    for org_id, driver_ids in open_orgs.items():
        # drivers with shifts: their own windows, around each shift
        school_drivers = []
        for driver_id in driver_ids:
            plan = plans.get(driver_id)
            if not (plan and plan.spans):
                school_drivers.append(driver_id)
                continue
            for w in plan.windows_on(today.weekday()):
                if starting(w) and once(f"tracking:{org_id}:{today:%Y%m%d}:d{driver_id}:{w.start}"):
                    notify.send_data(db, [driver_id], {"type": "tracking", "action": "start", "kind": w.kind})
                    told += 1
        # everybody else: the school's two windows, as before (never on Sundays)
        if not school_drivers or today.weekday() == SUNDAY:
            continue
        for w in windows_from(timings.get(org_id)):
            if starting(w) and once(f"tracking:{org_id}:{today:%Y%m%d}:{w.kind}"):
                notify.send_data(db, school_drivers, {"type": "tracking", "action": "start", "kind": w.kind})
                told += len(school_drivers)
    return told


# ------------------------------------------------- managers: minutes per driver (0027)

def _vehicle_for_settings(db: Db, vehicle_id: int) -> dict:
    row = db.one("SELECT v.id, v.vehicle_no, v.driver_id, u.full_name AS driver_name FROM vehicles v "
                 "LEFT JOIN users u ON u.id = v.driver_id WHERE v.id = %s AND v.org_id = %s", (vehicle_id, db.org))
    if not row:
        raise NotFound("Vehicle not found")
    return row


def vehicle_tracking(db: Db, vehicle_id: int) -> dict:
    """GET /vehicles/{id}/tracking: when this vehicle's driver shares the location."""
    vehicle = _vehicle_for_settings(db, vehicle_id)
    row = _timings_row(db, db.org) or {}
    school = {"before_minutes": _minutes_setting(row.get("tracking_before_minutes")),
              "after_minutes": _minutes_setting(row.get("tracking_after_minutes"))}
    now = school_now()
    sched = schedule(db, db.org)
    plan = driver_plan(db, db.org, vehicle["driver_id"])
    follows = bool(plan and plan.spans)
    return {
        "vehicle_id": vehicle["id"],
        "vehicle_no": vehicle["vehicle_no"],
        "driver_id": vehicle["driver_id"],
        "driver_name": vehicle["driver_name"],
        "before_minutes": plan.own_before if plan else None,
        "after_minutes": plan.own_after if plan else None,
        "school": school,
        "follows_shifts": follows,
        "holiday": sched.holiday,
        "windows": [w.as_dict() for w in (_day_windows(sched, plan, now.date()) if follows else sched.windows)],
        "upcoming": upcoming(db, db.org, sched, now, plan=plan)[:10] if vehicle["driver_id"] else [],
    }


def set_vehicle_tracking(db: Db, vehicle_id: int, body: TrackingMinutes, actor) -> dict:
    """PUT /vehicles/{id}/tracking: the driver's own minutes (null = the school's)."""
    with db.transaction():
        _save_minutes(db, vehicle_id, body, actor)
    forget_schedule(db.org)
    return vehicle_tracking(db, vehicle_id)


def _save_minutes(db: Db, vehicle_id: int, body: TrackingMinutes, actor) -> None:
    vehicle = _vehicle_for_settings(db, vehicle_id)
    if not vehicle["driver_id"]:
        raise BadRequest("Link a driver sign-in to this vehicle first.")
    db.execute(
        "INSERT INTO driver_tracking_settings (driver_id, org_id, before_minutes, after_minutes, updated_by) "
        "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE before_minutes = VALUES(before_minutes), "
        "after_minutes = VALUES(after_minutes), updated_by = VALUES(updated_by)",
        (vehicle["driver_id"], db.org, body.before_minutes, body.after_minutes, getattr(actor, "id", None)))
    audit.record(db, actor, "driver_tracking.update", "user", vehicle["driver_id"],
                 {"vehicle_id": vehicle_id, "before_minutes": body.before_minutes,
                  "after_minutes": body.after_minutes})


# ------------------------------------------------------------- clean-up hooks

def purge_statements(now: datetime) -> dict[str, tuple[str, tuple]]:
    """What the daily clean-up deletes for this feature (services/retention.py)."""
    return {
        "vehicle_location_history": (
            "DELETE FROM vehicle_location_history WHERE recorded_at < %s", (now - timedelta(days=HISTORY_DAYS),)),
        # a sign-in lives 180 days at most, so an older key can't work any more
        "driver_tracking_tokens": (
            "DELETE FROM driver_tracking_tokens WHERE created_at < %s", (now - timedelta(days=200),)),
        "scheduled_runs": (
            "DELETE FROM scheduled_runs WHERE name LIKE 'tracking:%%' AND last_run_at < %s",
            (now - timedelta(days=HISTORY_DAYS),)),
    }


