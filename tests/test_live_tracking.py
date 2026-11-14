"""Live bus tracking (migration 0023): the driver's phone reports only inside the
school's windows, parents see only their own child's bus, watching makes the
driver report faster, and old points are purged."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from helpers import bearer, login
from test_driver_routine import _driver, _vehicle
from test_organisations import _ok, create_school, login_to, next_weekday

from app.core.clock import SCHOOL_TZ
from app.services import live_tracking, notify, retention

JAIPUR = (26.912434, 75.787271)


class FakeClock:
    """School time, UTC and the process clock, moved by hand."""

    def __init__(self, monkeypatch):
        self.now = datetime.now(SCHOOL_TZ)
        self._mono = 1000.0
        monkeypatch.setattr(live_tracking, "school_now", lambda: self.now)
        monkeypatch.setattr(live_tracking, "utcnow", self.utc)
        monkeypatch.setattr(live_tracking, "clock", self)

    def utc(self) -> datetime:
        return self.now.astimezone(UTC).replace(tzinfo=None)

    def monotonic(self) -> float:
        return self._mono

    def set(self, day, hhmm: str) -> None:
        hours, minutes = map(int, hhmm.split(":"))
        target = datetime(day.year, day.month, day.day, hours, minutes, tzinfo=SCHOOL_TZ)
        self._mono += abs((target - self.now).total_seconds()) + 3600  # every memory has run out
        self.now = target

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self._mono += seconds


@pytest.fixture
def at(monkeypatch):
    return FakeClock(monkeypatch)


def _point(at, seconds_ago=0, lat=JAIPUR[0], lng=JAIPUR[1], **extra):
    when = at.utc() - timedelta(seconds=seconds_ago)
    return {"latitude": lat, "longitude": lng, "recorded_at": when.replace(tzinfo=UTC).isoformat(), **extra}


def _upload(client, who, at, points=None, **body):
    return client.post("/driver/location", headers=bearer(who),
                       json={"points": points or [_point(at, speed=6.5, heading=90, accuracy=8)], **body})


def _live(client, who):
    return _ok(client.get("/student/transport/live", headers=bearer(who)))


def _ride(client, school, vehicle, student_id):
    _ok(client.put(f"/vehicles/{vehicle}/students/{student_id}", headers=bearer(school.admin),
                   json={"stop_name": "Gate 2", "pickup_time": "07:10"}))


@pytest.fixture
def bus(client, school):
    """The seeded driver with one van; student 1 rides it."""
    driver = login(client, "driver")
    van = _vehicle(client, school.admin, "RJ14 AB 1001", driver["user_id"])
    _ride(client, school, van, school.s1)
    return driver, van, login(client, "student1", "studpass")


def _rows(db, table, vehicle):
    return db.all(f"SELECT * FROM {table} WHERE org_id = %s AND vehicle_id = %s", (db.org, vehicle))


# ------------------------------------------------------------------ windows

def test_driver_is_tracked_only_inside_the_windows(client, school, bus, at, db):
    driver, van, _ = bus
    monday = next_weekday(0)

    at.set(monday, "10:00")  # between the windows (no timings saved: 06:00-08:00 and 14:00-16:00)
    out = _ok(_upload(client, driver, at))
    assert (out["accepted"], out["watched"], out["window"]["active"]) == (0, False, False)
    assert out["window"]["next_start"].startswith(f"{monday}T14:00")
    assert 9 <= out["interval_sec"] <= 900
    assert _rows(db, "vehicle_locations", van) == [] and _rows(db, "vehicle_location_history", van) == []

    at.set(monday, "06:30")
    out = _ok(_upload(client, driver, at))
    assert (out["accepted"], out["interval_sec"], out["watched"], out["vehicle_id"]) == (1, 50, False, van)
    assert (out["window"]["active"], out["window"]["trip"]) == (True, "pickup")
    assert out["window"]["ends_at"].startswith(f"{monday}T08:00")
    row, = _rows(db, "vehicle_locations", van)
    assert (float(row["latitude"]), float(row["longitude"]), row["reported_by"]) == (*JAIPUR, driver["user_id"])
    assert len(_rows(db, "vehicle_location_history", van)) == 1

    at.set(monday, "15:59")
    assert _ok(_upload(client, driver, at))["window"]["trip"] == "drop"
    at.set(monday, "16:00")
    assert _ok(_upload(client, driver, at))["accepted"] == 0

    at.set(next_weekday(6), "06:30")  # Sunday
    assert _ok(_upload(client, driver, at))["window"]["active"] is False

    tuesday = next_weekday(1)
    _ok(client.post("/holidays", headers=bearer(school.admin),
                    json={"date": str(tuesday), "name": "Founders' Day"}), 201)
    at.set(tuesday, "06:30")
    out = _ok(_upload(client, driver, at))
    assert (out["accepted"], out["window"]["active"], out["window"]["holiday"]) == (0, False, "Founders' Day")


def test_windows_follow_the_school_timings(client, school, bus, at):
    driver, _, _ = bus
    admin = bearer(school.admin)
    timings = {"start_time": "08:00", "period_minutes": 45, "periods_per_day": 8}  # the day ends 14:00
    saved = _ok(client.put("/settings/school-timings", headers=admin, json=timings))  # an older client: no minutes
    assert (saved["tracking_before_minutes"], saved["tracking_after_minutes"]) == (120, 120)
    assert [(w["kind"], w["start"], w["end"]) for w in saved["tracking_windows"]] == [
        ("morning", "06:00", "08:00"), ("afternoon", "14:00", "16:00")]

    saved = _ok(client.put("/settings/school-timings", headers=admin, json={
        **timings, "tracking_before_minutes": 45, "tracking_after_minutes": 0}))
    assert [(w["kind"], w["start"], w["end"]) for w in saved["tracking_windows"]] == [("morning", "07:15", "08:00")]
    # ...and an older client saving the timings again keeps the school's choice
    again = _ok(client.put("/settings/school-timings", headers=admin, json=timings))
    assert (again["tracking_before_minutes"], again["tracking_after_minutes"]) == (45, 0)
    assert client.put("/settings/school-timings", headers=admin,
                      json={**timings, "tracking_before_minutes": 600}).status_code == 422

    monday = next_weekday(0)
    at.set(monday, "07:00")
    assert _ok(_upload(client, driver, at))["accepted"] == 0
    at.set(monday, "07:20")
    assert _ok(_upload(client, driver, at))["accepted"] == 1
    at.set(monday, "14:30")
    assert _ok(_upload(client, driver, at))["accepted"] == 0

    config = _ok(client.get("/driver/tracking/config", headers=bearer(driver)))
    assert config["enabled"] and config["vehicle"]["vehicle_no"] == "RJ14 AB 1001"
    assert [(w["start"], w["end"]) for w in config["windows"]] == [("07:15", "08:00")]
    assert config["active_now"] is False and config["intervals"]["upload_sec"] == 50
    assert config["upcoming"][0]["start_at"].startswith(f"{next_weekday(1)}T07:15")
    assert all(not u["start_at"].startswith(str(next_weekday(6))) for u in config["upcoming"])


# --------------------------------------------------------------- validation

def test_upload_checks_and_who_may_send(client, school, bus, at, db):
    driver, van, student = bus
    at.set(next_weekday(0), "06:30")
    h = bearer(driver)
    assert client.post("/driver/location", json={"points": [_point(at)]}).status_code == 401
    for other in (school.admin, school.t1, student):
        assert _upload(client, other, at).status_code == 403
    for bad in ({"points": []}, {"points": [_point(at)] * 21}, {"points": [_point(at, lat=91)]},
                {"points": [_point(at, lng=-181)]}, {"points": [{"latitude": 1, "longitude": 2}]},
                {"points": [_point(at, speed=-1)]}):
        assert client.post("/driver/location", headers=h, json=bad).status_code == 422, bad

    # a fix from the future or from long ago is dropped, the rest of the batch counts
    batch = [_point(at, -600), _point(at, 900), _point(at, 20, lat=26.9), _point(at, 40)]
    out = _ok(_upload(client, driver, at, batch))
    assert out["accepted"] == 2
    row, = _rows(db, "vehicle_locations", van)
    assert float(row["latitude"]) == 26.9  # the newest good one, whatever the order
    assert _ok(_upload(client, driver, at, [_point(at, -600)]))["accepted"] == 0
    # an older fix never replaces a newer one
    _ok(_upload(client, driver, at, [_point(at, 120, lat=10.0)]))
    assert float(_rows(db, "vehicle_locations", van)[0]["latitude"]) == 26.9
    # Unix milliseconds work too (what the phone's service sends)
    ms = int(at.utc().replace(tzinfo=UTC).timestamp() * 1000)
    assert _ok(_upload(client, driver, at, [{"latitude": 27.0, "longitude": 75.8, "recorded_at": ms}]))["accepted"] == 1
    assert float(_rows(db, "vehicle_locations", van)[0]["latitude"]) == 27.0

    # a driver without a vehicle is simply told there is nothing to do
    _, idle = _driver(client, school.admin, "driver9", "No Van")
    out = _ok(_upload(client, idle, at))
    assert (out["accepted"], out["vehicle_id"]) == (0, None)
    assert _ok(client.get("/driver/tracking/config", headers=bearer(idle)))["enabled"] is False
    # and can't report for someone else's
    assert _upload(client, idle, at, vehicle_id=van).status_code == 404
    assert _upload(client, driver, at, vehicle_id=van).status_code == 200


def test_uploads_are_rate_limited_per_driver(client, school, bus, at):
    driver, _, _ = bus
    at.set(next_weekday(0), "06:30")
    for _ in range(live_tracking.RATE_MAX):
        assert _upload(client, driver, at).status_code == 200
    blocked = _upload(client, driver, at)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
    at.tick(61)
    assert _upload(client, driver, at).status_code == 200


# ------------------------------------------------------------------ parents

def test_parent_states_and_watching_speeds_the_driver_up(client, school, bus, at):
    driver, van, parent = bus
    monday = next_weekday(0)
    not_on_bus = login(client, "student4", "studpass")
    for other in (school.admin, school.t1, driver):
        assert client.get("/student/transport/live", headers=bearer(other)).status_code == 403

    at.set(monday, "10:00")
    out = _live(client, parent)
    assert (out["state"], out["position"], out["vehicle"]["vehicle_no"]) == ("outside_window", None, "RJ14 AB 1001")
    assert out["window"]["next_start"].startswith(f"{monday}T14:00")
    out = _live(client, not_on_bus)
    assert (out["state"], out["vehicle"], out["position"]) == ("no_vehicle", None, None)

    at.set(monday, "06:30")
    assert _ok(_upload(client, driver, at))["interval_sec"] == 50   # nobody watching yet
    at.tick(20)
    out = _live(client, parent)
    assert (out["state"], out["age_sec"], out["last_seen_min"], out["moving"], out["poll_sec"]) == \
        ("live", 20, 0, True, 9)
    assert (out["position"]["latitude"], out["position"]["longitude"], out["position"]["heading"]) == (*JAIPUR, 90.0)
    assert out["position"]["recorded_at"].startswith(f"{monday}T06:30:00")
    assert "driver" not in str(out).lower()  # nothing about the driver here
    assert _live(client, not_on_bus)["state"] == "no_vehicle"

    # the driver's very next upload learns that someone is watching
    out = _ok(_upload(client, driver, at))
    assert (out["interval_sec"], out["watched"]) == (9, True)
    # the parent keeps polling every 9 s: still watched
    for _ in range(5):
        at.tick(9)
        assert _live(client, parent)["state"] == "live"
        assert _ok(_upload(client, driver, at))["watched"] is True
    # the app vanished without saying stop: the mark runs out by itself
    at.tick(live_tracking.WATCH_SECONDS + 6)
    assert _ok(_upload(client, driver, at))["interval_sec"] == 50

    # the phone goes quiet: stale, with how long ago
    at.tick(200)
    out = _live(client, parent)
    assert (out["state"], out["last_seen_min"]) == ("stale", 3)
    assert out["position"]["latitude"] == JAIPUR[0]  # where it was last seen in this window

    # the Track screen closes properly: slow rate at once
    assert _ok(_upload(client, driver, at))["watched"] is True
    assert _ok(client.post("/student/transport/live/stop", headers=bearer(parent))) == {"stopped": True}
    assert _ok(_upload(client, driver, at))["watched"] is False
    assert client.post("/student/transport/live/stop", headers=bearer(driver)).status_code == 403

    # afternoon window, the phone has not reported yet: the morning's position is NOT shown
    at.set(monday, "14:10")
    out = _live(client, parent)
    assert (out["state"], out["position"], out["age_sec"]) == ("stale", None, None)
    # and after the window nothing at all
    at.set(monday, "16:05")
    assert _live(client, parent)["position"] is None


def test_live_limit_is_tighter_once_the_fast_rate_runs(client, school, bus, at):
    driver, _, parent = bus
    at.set(next_weekday(0), "06:30")
    _ok(_upload(client, driver, at))
    at.tick(100)
    assert _live(client, parent)["state"] == "live"   # baseline: up to 120 s old is live
    for _ in range(8):                                # watched for more than a minute, no new fix
        at.tick(9)
        _live(client, parent)
    _ok(_upload(client, driver, at))
    states = []
    for _ in range(11):                               # the parent keeps watching, the phone went quiet
        at.tick(9)
        states.append(_live(client, parent)["state"])
    assert states == ["live"] * 10 + ["stale"]        # boosted: more than 90 s old is stale


def test_one_parent_stopping_does_not_slow_a_bus_others_watch(client, school, bus, at):
    driver, van, parent = bus
    _ride(client, school, van, school.s2)
    second = login(client, "student2", "studpass")
    at.set(next_weekday(0), "06:30")
    _ok(_upload(client, driver, at))
    _live(client, parent)
    at.tick(3)
    _live(client, second)
    assert _ok(client.post("/student/transport/live/stop", headers=bearer(parent))) == {"stopped": False}
    assert _ok(_upload(client, driver, at))["watched"] is True


def test_the_driver_is_nudged_when_watching_starts(client, school, bus, at, monkeypatch):
    driver, van, parent = bus
    sent = []
    monkeypatch.setattr(notify, "send_data", lambda db, users, data: sent.append((list(users), data)))
    at.set(next_weekday(0), "06:30")
    _ok(_upload(client, driver, at))
    _live(client, parent)
    assert sent == [([driver["user_id"]], {"type": "tracking", "action": "boost", "vehicle_id": str(van)})]
    for _ in range(4):
        at.tick(9)
        _live(client, parent)
    assert len(sent) == 1                      # only when watching STARTS
    at.tick(60)
    _live(client, parent)
    assert len(sent) == 2                      # it had run out: nudged again
    at.set(next_weekday(0), "10:00")
    _live(client, parent)
    assert len(sent) == 2                      # outside the window nobody is woken


def test_silent_push_is_a_data_only_message(school, bus, monkeypatch, db):
    messaging = pytest.importorskip("firebase_admin.messaging")
    driver, _, _ = bus
    db.execute("INSERT INTO device_tokens (user_id, token, platform) VALUES (%s, 'tok-1', 'android')",
               (driver["user_id"],))
    seen = []

    class Result:
        responses = []

    def send_each(messages, app=None):
        seen.extend(messages)
        result = Result()
        result.responses = [type("R", (), {"success": True, "exception": None})() for _ in messages]
        return result

    monkeypatch.setattr(notify, "push_configured", lambda: True)
    monkeypatch.setattr(notify, "_firebase_app", lambda: object())
    monkeypatch.setattr(messaging, "send_each", send_each)
    notify.send_data(db, [driver["user_id"]], {"type": "tracking", "action": "boost", "vehicle_id": 5})
    message, = seen
    assert message.notification is None and message.android.priority == "high"
    assert message.data == {"type": "tracking", "action": "boost", "vehicle_id": "5"}
    assert db.scalar("SELECT COUNT(*) FROM notifications") == 0  # nothing in anyone's inbox


def test_window_start_wakes_the_drivers_once(client, school, bus, at, monkeypatch, db):
    driver, _, _ = bus
    sent = []
    monkeypatch.setattr(notify, "push_configured", lambda: True)
    monkeypatch.setattr(notify, "send_data", lambda db, users, data: sent.append((sorted(users), data["action"],
                                                                                  data["kind"])))
    plain = live_tracking.Db(live_tracking.pool)  # like the worker: not bound to a school
    try:
        monday = next_weekday(0)
        at.set(monday, "05:59")
        assert live_tracking.announce_window_starts(plain, at.now) == 0
        at.set(monday, "06:01")
        assert live_tracking.announce_window_starts(plain, at.now) == 1
        assert live_tracking.announce_window_starts(plain, at.now) == 0   # once per window
        at.set(monday, "06:30")
        assert live_tracking.announce_window_starts(plain, at.now) == 0   # long started
        at.set(monday, "14:02")
        assert live_tracking.announce_window_starts(plain, at.now) == 1
        assert sent == [([driver["user_id"]], "start", "morning"), ([driver["user_id"]], "start", "afternoon")]
        at.set(next_weekday(6), "06:01")
        assert live_tracking.announce_window_starts(plain, at.now) == 0   # Sunday
    finally:
        plain.close()


# ------------------------------------------------------------ upload-only key

def test_tracking_token_can_only_send_locations_and_ends_with_the_sign_in(client, school, bus, at, db):
    driver, van, _ = bus
    at.set(next_weekday(0), "06:30")
    for other in (school.admin, school.t1):
        assert client.post("/driver/tracking/token", headers=bearer(other)).status_code == 403
    token = _ok(client.post("/driver/tracking/token", headers=bearer(driver)))["token"]
    assert token.startswith("tk1.") and token not in str(db.all("SELECT * FROM driver_tracking_tokens"))

    assert _ok(_upload(client, token, at))["accepted"] == 1
    assert _rows(db, "vehicle_locations", van)[0]["reported_by"] == driver["user_id"]
    for path in ("/driver/routine", "/driver/tracking/config", "/auth/me", "/vehicles"):
        assert client.get(path, headers=bearer(token)).status_code == 401, path
    assert _upload(client, token + "x", at).status_code == 401
    assert _upload(client, "tk1.", at).status_code == 401

    # asking again replaces it (one per sign-in)
    newer = _ok(client.post("/driver/tracking/token", headers=bearer(driver)))["token"]
    assert _upload(client, token, at).status_code == 401
    assert _upload(client, newer, at).status_code == 200

    # transport switched off for the school: the key stops too
    hq = {"X-Platform-Secret": "test-platform-secret"}
    _ok(client.patch("/platform/organisations/DEFAULT/features", headers=hq, json={"changes": {"transport": False}}))
    blocked = _upload(client, newer, at)
    assert blocked.status_code == 403 and blocked.json()["code"] == "feature_disabled"
    parent = login(client, "student1", "studpass")
    assert client.get("/student/transport/live", headers=bearer(parent)).status_code == 403
    _ok(client.patch("/platform/organisations/DEFAULT/features", headers=hq, json={"changes": {"transport": None}}))
    assert _upload(client, newer, at).status_code == 200

    # signing out ends it
    assert client.post("/auth/logout", headers=bearer(driver),
                       json={"refresh_token": driver["refresh_token"]}).status_code in (200, 204)
    assert _upload(client, newer, at).status_code == 401


# ---------------------------------------------------------------- isolation

def test_a_parent_never_sees_another_childs_or_another_schools_bus(client, school, bus, at, db):
    driver, van, parent = bus
    other_id, other_driver = _driver(client, school.admin)
    other_van = _vehicle(client, school.admin, "RJ14 AB 2002", other_id)
    _ride(client, school, other_van, school.s3)
    other_parent = login(client, "student3", "studpass")

    # a second school with the same usernames and its own bus
    create_school(client, "BETA", "Beta School", "beta-admin-pass")
    b_admin = _ok(login_to(client, "BETA", "admin", "beta-admin-pass"))
    hb = bearer(b_admin)
    b_driver_id = _ok(client.post("/users", headers=hb, json={
        "username": "driver", "password": "drivepass", "full_name": "Beta Driver", "user_type": "driver"}), 201)["id"]
    b_driver = _ok(login_to(client, "BETA", "driver", "drivepass"))
    b_van = _vehicle(client, b_admin, "RJ14 AB 1001", b_driver_id)   # same number plate as school A's
    b_class = _ok(client.post("/classes", headers=hb, json={"name": "Class 1"}), 201)
    b_section = _ok(client.post(f"/classes/{b_class['id']}/sections", headers=hb, json={"name": "A"}), 201)
    b_student = _ok(client.post("/students", headers=hb, json={
        "username": "student1", "password": "studpass", "full_name": "Beta Student", "roll_no": "1",
        "admission_no": "ADM1", "class_id": b_class["id"], "section_id": b_section["id"]}), 201)["id"]
    _ok(client.put(f"/vehicles/{b_van}/students/{b_student}", headers=hb, json={"stop_name": "Beta Stop"}))
    b_parent = _ok(login_to(client, "BETA", "student1", "studpass"))

    at.set(next_weekday(0), "06:30")
    _ok(_upload(client, driver, at, [_point(at, lat=11.0, lng=71.0)]))
    _ok(_upload(client, other_driver, at, [_point(at, lat=22.0, lng=72.0)]))
    _ok(_upload(client, b_driver, at, [_point(at, lat=33.0, lng=73.0)]))

    seen = {name: _live(client, who) for name, who in
            (("a1", parent), ("a3", other_parent), ("b1", b_parent))}
    assert {k: (v["state"], v["vehicle"]["id"], v["position"]["latitude"]) for k, v in seen.items()} == {
        "a1": ("live", van, 11.0), "a3": ("live", other_van, 22.0), "b1": ("live", b_van, 33.0)}

    # a driver can't report for a vehicle of another driver or another school: both just "not found"
    for who, vehicle in ((driver, other_van), (driver, b_van), (b_driver, van), (other_driver, van)):
        assert _upload(client, who, at, vehicle_id=vehicle).status_code == 404
    token_b = _ok(client.post("/driver/tracking/token", headers=bearer(b_driver)))["token"]
    assert _upload(client, token_b, at, vehicle_id=van).status_code == 404
    assert _ok(_upload(client, token_b, at))["vehicle_id"] == b_van
    # nothing leaked between the rows
    assert {r["vehicle_id"]: r["org_id"] for r in db.all("SELECT vehicle_id, org_id FROM vehicle_locations")} == {
        van: 1, other_van: 1, b_van: b_admin["org_id"]}

    # watching one bus speeds up only that bus
    assert _ok(_upload(client, driver, at))["watched"] is True
    at.tick(40)
    _live(client, b_parent)
    assert _ok(_upload(client, driver, at))["watched"] is False
    assert _ok(_upload(client, b_driver, at))["watched"] is True

    # the child is moved to the other bus: the parent now sees that one
    _ride(client, school, other_van, school.s1)
    at.tick(20)
    assert _live(client, parent)["vehicle"]["id"] == other_van
    # and taken off transport: nothing
    assert client.delete(f"/vehicles/{other_van}/students/{school.s1}", headers=bearer(school.admin)).status_code < 300
    at.tick(20)
    assert _live(client, parent)["state"] == "no_vehicle"


# ------------------------------------------------------------------ history

def test_history_is_sampled_and_purged_after_a_week(client, school, bus, at, db):
    driver, van, _ = bus
    at.set(next_weekday(0), "06:30")
    for _ in range(11):   # 100 s of the fast rate
        _ok(_upload(client, driver, at))
        at.tick(10)
    kept = _rows(db, "vehicle_location_history", van)
    assert len(kept) == 3   # 0 s, 50 s, 100 s: about one a minute, not every upload
    assert len(_rows(db, "vehicle_locations", van)) == 1

    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
    recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=6)
    db.execute("UPDATE vehicle_location_history SET recorded_at = %s WHERE id = %s", (old, kept[0]["id"]))
    db.execute("UPDATE vehicle_location_history SET recorded_at = %s WHERE id = %s", (recent, kept[1]["id"]))
    db.execute("INSERT INTO scheduled_runs (name, last_run_at) VALUES ('tracking:1:20200101:morning', %s)", (old,))
    removed = retention.run(db)
    assert removed["vehicle_location_history"] == 1 and removed["scheduled_runs"] == 1
    assert {r["id"] for r in _rows(db, "vehicle_location_history", van)} == {kept[1]["id"], kept[2]["id"]}
    assert db.scalar("SELECT COUNT(*) FROM scheduled_runs WHERE name = 'retention'") in (0, 1)

    # deleting the vehicle takes its position and history with it
    assert client.delete(f"/vehicles/{van}", headers=bearer(school.admin)).status_code < 300
    assert db.scalar("SELECT COUNT(*) FROM vehicle_locations") == 0
    assert db.scalar("SELECT COUNT(*) FROM vehicle_location_history") == 0
