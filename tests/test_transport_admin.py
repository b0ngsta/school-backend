"""Transport set-up from the admin screens (15 Nov 2026, no migration):
Add / Edit vehicle can create the driver's sign-in in the same request
(`new_driver`), and a student's profile shows / changes their vehicle and
their attendance month calendar."""
from __future__ import annotations

from helpers import bearer, login
from test_organisations import _ok

from app.core.clock import school_today


def _vehicle_body(no="RJ10 9234", **extra):
    return {"vehicle_no": no, "driver_name": "Badri Lal", "driver_phone": "9811111111", "vehicle_type": "van",
            **extra}


def test_add_vehicle_creates_the_driver_account_too(client, school, db):
    admin = bearer(school.admin)
    made = _ok(client.post("/vehicles", headers=admin, json=_vehicle_body(
        new_driver={"username": "badri", "password": "drive123"})), 201)
    assert made["driver_id"]
    row = next(v for v in _ok(client.get("/vehicles", headers=admin)) if v["id"] == made["id"])
    assert (row["driver_id"], row["driver_account_name"]) == (made["driver_id"], "Badri Lal")
    # a real driver sign-in: name / phone from the vehicle form
    driver = login(client, "badri", "drive123")
    me = _ok(client.get("/auth/me", headers=bearer(driver)))
    assert (me["user_type"], me["full_name"]) == ("driver", "Badri Lal")
    assert db.scalar("SELECT phone FROM users WHERE id = %s", (made["driver_id"],)) == "9811111111"
    assert db.scalar("SELECT COUNT(*) FROM audit_logs WHERE action = 'user.create' AND entity_id = %s",
                     (made["driver_id"],)) == 1
    # the driver sees their vehicle in My Routine
    assert [v["vehicle_no"] for v in _ok(client.get("/driver/routine", headers=bearer(driver)))["vehicles"]] == \
        ["RJ10 9234"]


def test_new_driver_problems_save_nothing(client, school, db):
    admin = bearer(school.admin)
    # username taken → 409, and no vehicle either (one transaction)
    r = client.post("/vehicles", headers=admin, json=_vehicle_body(
        new_driver={"username": "teacher1", "password": "drive123"}))
    assert r.status_code == 409, r.text
    assert db.scalar("SELECT COUNT(*) FROM vehicles WHERE vehicle_no = 'RJ10 9234'") == 0
    # vehicle number taken → 409, and no driver account left behind
    _ok(client.post("/vehicles", headers=admin, json=_vehicle_body("RJ10 0001")), 201)
    r = client.post("/vehicles", headers=admin, json=_vehicle_body(
        "RJ10 0001", new_driver={"username": "ghost", "password": "drive123"}))
    assert r.status_code == 409, r.text
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username = 'ghost'") == 0
    # both an existing account and a new one → 400; short password → 422
    r = client.post("/vehicles", headers=admin, json=_vehicle_body(
        driver_id=1, new_driver={"username": "both", "password": "drive123"}))
    assert r.status_code == 400, r.text
    r = client.post("/vehicles", headers=admin, json=_vehicle_body(
        new_driver={"username": "shorty", "password": "123"}))
    assert r.status_code == 422, r.text
    # teachers can't add vehicles at all
    r = client.post("/vehicles", headers=bearer(school.t1), json=_vehicle_body(
        new_driver={"username": "sneaky", "password": "drive123"}))
    assert r.status_code == 403
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username IN ('both', 'shorty', 'sneaky')") == 0


def test_edit_vehicle_can_create_and_link_a_new_driver(client, school):
    admin = bearer(school.admin)
    vid = _ok(client.post("/vehicles", headers=admin, json=_vehicle_body("RJ10 4562")), 201)["id"]
    res = _ok(client.put(f"/vehicles/{vid}", headers=admin, json={
        "driver_name": "Mohan", "new_driver": {"username": "mohan", "password": "drive123"}}))
    assert res["updated"] == vid and res["driver_id"]
    row = next(v for v in _ok(client.get("/vehicles", headers=admin)) if v["id"] == vid)
    assert (row["driver_id"], row["driver_name"], row["driver_account_name"]) == (res["driver_id"], "Mohan", "Mohan")
    # an ordinary edit leaves the driver alone
    _ok(client.put(f"/vehicles/{vid}", headers=admin, json={"route_name": "City Centre"}))
    row = next(v for v in _ok(client.get("/vehicles", headers=admin)) if v["id"] == vid)
    assert (row["driver_id"], row["route_name"]) == (res["driver_id"], "City Centre")


def test_student_profile_shows_and_changes_transport(client, school):
    admin = bearer(school.admin)
    vid = _ok(client.post("/vehicles", headers=admin, json=_vehicle_body("RJ10 7777", route_name="Civil Lines")),
              201)["id"]
    assert _ok(client.get(f"/students/{school.s1}/transport", headers=admin)) is None
    _ok(client.put(f"/vehicles/{vid}/students/{school.s1}", headers=admin,
                   json={"stop_name": "Gate 2", "pickup_time": "07:10", "drop_time": "14:20"}))
    got = _ok(client.get(f"/students/{school.s1}/transport", headers=admin))
    assert (got["vehicle_id"], got["vehicle_no"], got["route_name"], got["stop_name"], got["pickup_time"],
            got["drop_time"]) == (vid, "RJ10 7777", "Civil Lines", "Gate 2", "07:10", "14:20")
    # teachers of the section see it; a teacher of another class does not
    assert _ok(client.get(f"/students/{school.s1}/transport", headers=bearer(school.t1)))["vehicle_id"] == vid
    assert client.get(f"/students/{school.s1}/transport", headers=bearer(school.t3)).status_code == 403
    _ok(client.delete(f"/vehicles/{vid}/students/{school.s1}", headers=admin), 204)
    assert _ok(client.get(f"/students/{school.s1}/transport", headers=admin)) is None


def test_student_profile_attendance_month(client, school):
    admin = bearer(school.admin)
    today = school_today()
    if today.weekday() != 6:
        _ok(client.post("/attendance", headers=bearer(school.t1), json={
            "section_id": school.c1a, "date": str(today),
            "records": [{"student_id": school.s1, "status": "present"},
                        {"student_id": school.s2, "status": "absent"}]}))
    month = _ok(client.get(f"/students/{school.s1}/attendance/month?year={today.year}&month={today.month}",
                           headers=admin))
    assert (month["year"], month["month"]) == (today.year, today.month)
    day = next(d for d in month["days"] if d["date"] == str(today))
    assert day["status"] == ("sunday" if today.weekday() == 6 else "present")
    overview = _ok(client.get(f"/students/{school.s1}/attendance/overview", headers=admin))
    assert "totals" in overview and overview["months"]
    assert client.get(f"/students/{school.s1}/attendance/month?year={today.year}&month=13",
                      headers=admin).status_code == 400
    assert client.get(f"/students/{school.s1}/attendance/month?year={today.year}&month={today.month}",
                      headers=bearer(school.t3)).status_code == 403
