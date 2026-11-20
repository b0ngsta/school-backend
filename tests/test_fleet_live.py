"""Live status of every bus for managers (GET /vehicles/live): Live / Last seen N min /
Inactive with a reason, the position only inside the window, and a manager's map
makes the driver report fast like a watching parent."""
from __future__ import annotations

from helpers import bearer, login
from test_driver_routine import _driver, _vehicle
from test_live_tracking import _upload, at  # noqa: F401  (fixture)
from test_organisations import _ok, next_weekday

from app.services import notify


def _fleet(client, who, watch=None):
    return _ok(client.get("/vehicles/live" + (f"?watch={watch}" if watch else ""), headers=bearer(who)))


def _row(out, vehicle):
    return next(v for v in out["vehicles"] if v["id"] == vehicle)


def test_managers_see_every_bus_and_its_state(client, school, at):
    admin = school.admin
    driver = login(client, "driver")
    van = _vehicle(client, admin, "RJ14 AB 3001", driver["user_id"])
    plain = _vehicle(client, admin, "RJ14 AB 3002", None)
    monday = next_weekday(0)

    at.set(monday, "10:00")   # between the school's windows
    out = _fleet(client, admin)
    assert out["poll_sec"] == 30 and out["live"] == 0
    row = _row(out, van)
    assert (row["state"], row["reason"], row["position"]) == ("inactive", "outside_window", None)
    assert [w["start"] for w in row["windows"]] == ["06:00", "14:00"]
    assert _row(out, plain)["reason"] == "no_sign_in"

    at.set(monday, "06:30")
    assert _row(_fleet(client, admin), van)["reason"] == "no_data"
    _ok(_upload(client, driver, at))
    row = _row(_fleet(client, admin), van)
    assert (row["state"], row["age_sec"], row["window"]["trip"]) == ("live", 0, "pickup")
    assert row["position"]["latitude"] == 26.912434 and row["last_seen_at"]

    at.tick(200)              # the phone went quiet
    row = _row(_fleet(client, admin), van)
    assert (row["state"], row["last_seen_min"]) == ("stale", 3)

    at.set(monday, "10:00")   # outside the window: no position any more, only when it was last seen
    row = _row(_fleet(client, admin), van)
    assert (row["state"], row["position"], row["reason"]) == ("inactive", None, "outside_window")
    assert row["last_seen_at"]


def test_a_manager_map_speeds_up_the_driver(client, school, at, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_data", lambda db, users, data: sent.append((list(users), data["action"])))
    driver = login(client, "driver")
    van = _vehicle(client, school.admin, "RJ14 AB 3003", driver["user_id"])
    at.set(next_weekday(0), "06:30")
    assert _ok(_upload(client, driver, at))["interval_sec"] == 50
    _fleet(client, school.admin)                            # the list alone: no watching
    assert _ok(_upload(client, driver, at))["watched"] is False and sent == []
    out = _fleet(client, school.admin, f"{van},999999,abc")  # the map: other ids are ignored
    assert out["poll_sec"] == 9
    assert sent == [([driver["user_id"]], "boost")]
    up = _ok(_upload(client, driver, at))
    assert (up["watched"], up["interval_sec"]) == (True, 9)
    at.tick(30)                                              # the map was closed: back to the slow rate
    assert _ok(_upload(client, driver, at))["interval_sec"] == 50


def test_only_managers_and_only_their_school(client, school):
    other_id, _ = _driver(client, school.admin, "driver9", "Other Driver")
    _vehicle(client, school.admin, "RJ14 AB 3004", other_id)
    for who in (login(client, "teacher1", "teachpass"), login(client, "driver"), login(client, "student1", "studpass")):
        assert client.get("/vehicles/live", headers=bearer(who)).status_code == 403
