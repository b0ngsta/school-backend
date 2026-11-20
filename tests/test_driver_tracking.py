"""Bus tracking per driver (migration 0027): a driver with shifts shares the location
around each shift (X minutes before its start, Y after its end, overlaps merged);
X / Y are the driver's own minutes or the school's default; a driver without shifts
keeps the school's two windows exactly as before."""
from __future__ import annotations

from helpers import bearer, login
from test_driver_routine import _driver, _shift, _vehicle
from test_live_tracking import _live, _ride, _upload, at  # noqa: F401  (fixture)
from test_organisations import _ok, next_weekday

from app.services import live_tracking, notify
from app.services.live_tracking import DriverPlan, ShiftSpan, Window

WEEK = frozenset(range(6))


def _hm(w):
    return (w["kind"], w["start"], w["end"])


def test_windows_around_each_shift_merge_when_they_overlap():
    plan = DriverPlan(spans=(ShiftSpan("pickup", 7 * 60, 8 * 60, WEEK),
                             ShiftSpan("pickup", 8 * 60 + 30, 9 * 60, WEEK),
                             ShiftSpan("drop", 14 * 60, 15 * 60, frozenset({0, 2}))),
                      before=45, after=30)
    assert plan.windows_on(0) == (Window("morning", 375, 570), Window("afternoon", 795, 930))
    assert plan.windows_on(1) == (Window("morning", 375, 570),)   # the drop runs Mon / Wed only
    assert plan.windows_on(6) == ()                                 # no shift on Sunday
    late = DriverPlan(spans=(ShiftSpan("drop", 23 * 60, 23 * 60 + 50, WEEK),), before=0, after=120)
    assert late.windows_on(0) == (Window("afternoon", 23 * 60, 24 * 60 - 1),)   # never past midnight


def test_driver_with_shifts_shares_around_them(client, school, at):
    admin = school.admin
    driver = login(client, "driver")
    van = _vehicle(client, admin, "RJ14 AB 2001", driver["user_id"])
    _ride(client, school, van, school.s1)
    parent = login(client, "student1", "studpass")
    monday = next_weekday(0)

    # no shifts yet: the school's windows (no timings saved: 06:00-08:00 and 14:00-16:00)
    at.set(monday, "06:30")
    config = _ok(client.get("/driver/tracking/config", headers=bearer(driver)))
    assert config["follows_shifts"] is False and config["active_now"] is True
    assert [_hm(w) for w in config["windows"]] == [("morning", "06:00", "08:00"), ("afternoon", "14:00", "16:00")]

    _shift(client, admin, van, "Morning", "pickup", "07:30", "08:30")
    _shift(client, admin, van, "Evening", "drop", "15:00", "16:00", weekdays=[0, 1, 2])
    _shift(client, admin, van, "Old", "drop", "11:00", "12:00", is_active=False)   # switched off: ignored

    at.set(monday, "06:30")   # default 120 minutes around each shift
    config = _ok(client.get("/driver/tracking/config", headers=bearer(driver)))
    assert config["follows_shifts"] is True
    assert [_hm(w) for w in config["windows"]] == [("morning", "05:30", "10:30"), ("afternoon", "13:00", "18:00")]
    assert config["upcoming"][0]["start_at"].startswith(f"{monday}T05:30")

    # the driver's own minutes
    tracking = _ok(client.put(f"/vehicles/{van}/tracking", headers=bearer(admin),
                              json={"before_minutes": 15, "after_minutes": 10}))
    assert (tracking["before_minutes"], tracking["after_minutes"], tracking["follows_shifts"]) == (15, 10, True)
    assert tracking["school"] == {"before_minutes": 120, "after_minutes": 120}
    assert [_hm(w) for w in tracking["windows"]] == [("morning", "07:15", "08:40"), ("afternoon", "14:45", "16:10")]

    at.set(monday, "07:00")   # before the shift's window: nothing stored, the parent sees nothing
    assert _ok(_upload(client, driver, at))["accepted"] == 0
    assert _live(client, parent)["state"] == "outside_window"
    at.set(monday, "07:20")
    out = _ok(_upload(client, driver, at))
    assert (out["accepted"], out["window"]["trip"]) == (1, "pickup")
    assert out["window"]["ends_at"].startswith(f"{monday}T08:40")
    assert _live(client, parent)["state"] == "live"
    at.set(monday, "16:05")
    assert _ok(_upload(client, driver, at))["window"]["trip"] == "drop"

    thursday = next_weekday(3)   # the evening shift runs Mon-Wed only
    at.set(thursday, "16:05")
    assert _ok(_upload(client, driver, at))["accepted"] == 0

    # back to the school's minutes: null
    tracking = _ok(client.put(f"/vehicles/{van}/tracking", headers=bearer(admin),
                              json={"before_minutes": None, "after_minutes": 30}))
    assert (tracking["before_minutes"], tracking["after_minutes"]) == (None, 30)
    assert [_hm(w) for w in tracking["windows"]][0] == ("morning", "05:30", "09:00")

    # a holiday still stops it
    tuesday = next_weekday(1)
    _ok(client.post("/holidays", headers=bearer(admin), json={"date": str(tuesday), "name": "Fair"}), 201)
    at.set(tuesday, "07:45")
    assert _ok(_upload(client, driver, at))["accepted"] == 0


def test_a_new_shift_is_seen_at_once(client, school, at):
    driver = login(client, "driver")
    van = _vehicle(client, school.admin, "RJ14 AB 2002", driver["user_id"])
    monday = next_weekday(0)
    at.set(monday, "11:30")
    assert _ok(_upload(client, driver, at))["accepted"] == 0          # school windows: nothing at 11:30
    _shift(client, school.admin, van, "Noon", "drop", "12:00", "13:00")
    assert _ok(_upload(client, driver, at))["accepted"] == 1          # no waiting for the cache


def test_only_managers_set_the_minutes(client, school):
    admin = bearer(school.admin)
    driver = login(client, "driver")
    van = _vehicle(client, school.admin, "RJ14 AB 2003", driver["user_id"])
    no_sign_in = _vehicle(client, school.admin, "RJ14 AB 2004", None)

    out = _ok(client.get(f"/vehicles/{van}/tracking", headers=admin))
    assert (out["driver_id"], out["before_minutes"], out["follows_shifts"]) == (driver["user_id"], None, False)
    assert client.put(f"/vehicles/{van}/tracking", headers=admin, json={"before_minutes": 241}).status_code == 422
    assert client.put(f"/vehicles/{van}/tracking", headers=admin, json={"after_minutes": -1}).status_code == 422
    assert client.put(f"/vehicles/{no_sign_in}/tracking", headers=admin, json={"before_minutes": 10}).status_code == 400
    assert client.get("/vehicles/999999/tracking", headers=admin).status_code == 404
    for who in (login(client, "teacher1", "teachpass"), driver):
        assert client.get(f"/vehicles/{van}/tracking", headers=bearer(who)).status_code == 403
        assert client.put(f"/vehicles/{van}/tracking", headers=bearer(who), json={}).status_code == 403


def test_drivers_with_shifts_are_woken_at_their_own_start(client, school, at, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "push_configured", lambda: True)
    monkeypatch.setattr(notify, "send_data", lambda db, users, data: sent.append((sorted(users), data["kind"])))
    plain_driver = login(client, "driver")
    _vehicle(client, school.admin, "RJ14 AB 2005", plain_driver["user_id"])
    shifted_id, _ = _driver(client, school.admin)
    van = _vehicle(client, school.admin, "RJ14 AB 2006", shifted_id)
    _shift(client, school.admin, van, "Morning", "pickup", "07:30", "08:30")
    _ok(client.put(f"/vehicles/{van}/tracking", headers=bearer(school.admin), json={"before_minutes": 30}))

    plain = live_tracking.Db(live_tracking.pool)   # like the worker: not bound to a school
    try:
        monday = next_weekday(0)
        at.set(monday, "06:01")   # the school's window: only the driver without shifts
        assert live_tracking.announce_window_starts(plain, at.now) == 1
        at.set(monday, "07:01")   # the shift's window (07:30 - 30 min)
        assert live_tracking.announce_window_starts(plain, at.now) == 1
        assert live_tracking.announce_window_starts(plain, at.now) == 0   # once
        assert sent == [([plain_driver["user_id"]], "morning"), ([shifted_id], "morning")]
        at.set(next_weekday(6), "06:01")   # Sunday: no school window, the shift doesn't run either
        assert live_tracking.announce_window_starts(plain, at.now) == 0
    finally:
        plain.close()
