"""Driver "My Routine": shifts per vehicle with ordered students, who may see
them, the pre-0012 single-shift fallback, holidays / weekdays, and the
manager endpoints that set it up."""
from __future__ import annotations

from datetime import timedelta

from helpers import bearer, login
from test_organisations import _ok, create_school, login_to, next_weekday

from app.core.clock import school_today


def _driver(client, admin, username="driver2", name="Second Driver"):
    """A driver account made through the API; returns (id, login)."""
    user_id = _ok(client.post("/users", headers=bearer(admin), json={
        "username": username, "password": "drivepass", "full_name": name, "user_type": "driver"}), 201)["id"]
    return user_id, login(client, username, "drivepass")


def _vehicle(client, admin, no, driver_id, **extra):
    return _ok(client.post("/vehicles", headers=bearer(admin), json={
        "vehicle_no": no, "driver_name": "Ramesh", "driver_id": driver_id, "vehicle_type": "van", **extra}), 201)["id"]


def _shift(client, admin, vehicle, name, trip, start, end, **extra):
    return _ok(client.post(f"/vehicles/{vehicle}/shifts", headers=bearer(admin), json={
        "name": name, "trip_type": trip, "start_time": start, "end_time": end, **extra}), 201)["id"]


def _routine(client, who, day=None):
    path = "/driver/routine" + (f"?date={day}" if day else "")
    return _ok(client.get(path, headers=bearer(who)))


def test_driver_gets_own_shifts_with_students_in_stop_order(client, school):
    admin = school.admin
    driver = login(client, "driver")
    other_id, other = _driver(client, admin)
    van = _vehicle(client, admin, "RJ14 AB 1001", driver["user_id"], route_name="Civil Lines", capacity=25)
    other_van = _vehicle(client, admin, "RJ14 AB 2002", other_id)

    evening = _shift(client, admin, van, "Evening Shift 1", "drop", "14:00", "15:30")
    morning1 = _shift(client, admin, van, "Morning Shift 1", "pickup", "07:00", "08:30")
    morning2 = _shift(client, admin, van, "Morning Shift 2", "pickup", "09:00", "10:30")
    _shift(client, admin, van, "Old shift", "pickup", "06:00", "06:45", is_active=False)
    saturday_only = _shift(client, admin, van, "Saturday special", "pickup", "11:00", "11:30", weekdays=[5])
    theirs = _shift(client, admin, other_van, "Their shift", "pickup", "07:00", "08:00")

    _ok(client.put(f"/vehicles/{van}/shifts/{morning1}/students", headers=bearer(admin), json={"students": [
        {"student_id": school.s3, "stop_time": "07:10", "stop_name": "Gate 2", "landmark": "Near temple",
         "latitude": 26.912434, "longitude": 75.787271, "notes": "Peanut allergy"},
        {"student_id": school.s1, "stop_time": "07:25"},
    ]}))
    _ok(client.put(f"/vehicles/{van}/shifts/{evening}/students", headers=bearer(admin), json={"students": [
        {"student_id": school.s1}, {"student_id": school.s3}, {"student_id": school.s2}]}))
    _ok(client.put(f"/vehicles/{other_van}/shifts/{theirs}/students", headers=bearer(admin),
                   json={"students": [{"student_id": school.s4}]}))

    monday = next_weekday(0)
    day = _routine(client, driver, monday)
    assert (day["date"], day["weekday"], day["holiday"]) == (str(monday), 0, None)
    assert [v["vehicle_no"] for v in day["vehicles"]] == ["RJ14 AB 1001"]
    # sorted by start; switched-off and not-on-Monday shifts left out; other driver's never shown
    assert [(s["key"], s["name"], s["trip_type"], s["start_time"], s["end_time"]) for s in day["shifts"]] == [
        (f"s{morning1}", "Morning Shift 1", "pickup", "07:00", "08:30"),
        (f"s{morning2}", "Morning Shift 2", "pickup", "09:00", "10:30"),
        (f"s{evening}", "Evening Shift 1", "drop", "14:00", "15:30"),
    ]
    first = day["shifts"][0]
    assert first["student_count"] == 2 and first["vehicle"]["route_name"] == "Civil Lines"
    s3, s1 = first["students"]
    assert (s3["student_id"], s3["stop_no"], s3["stop_time"], s3["stop_name"]) == (school.s3, 1, "07:10", "Gate 2")
    assert (s3["landmark"], s3["latitude"], s3["longitude"], s3["notes"]) == \
        ("Near temple", 26.912434, 75.787271, "Peanut allergy")
    # details come from the student's profile
    assert (s3["full_name"], s3["class_name"], s3["section_name"], s3["address"], s3["father_name"],
            s3["guardian_phone"]) == ("Student 3", "Class 1", "B", "House 3", "Father 3", "700000003")
    assert (s1["student_id"], s1["stop_no"], s1["latitude"]) == (school.s1, 2, None)
    assert [s["student_id"] for s in day["shifts"][2]["students"]] == [school.s1, school.s3, school.s2]
    assert day["shifts"][1]["student_count"] == 0

    saturday = _routine(client, driver, next_weekday(5))
    assert f"s{saturday_only}" in [s["key"] for s in saturday["shifts"]]
    assert _routine(client, driver, next_weekday(6))["shifts"] == []  # Sunday

    mine = _routine(client, other, monday)
    assert [s["key"] for s in mine["shifts"]] == [f"s{theirs}"]
    assert [s["student_id"] for s in mine["shifts"][0]["students"]] == [school.s4]

    today = _routine(client, driver)
    assert today["date"] == str(school_today()) and today["is_today"] is True


def test_only_drivers_can_open_the_routine(client, school):
    assert client.get("/driver/routine").status_code == 401
    for who in (school.admin, school.t1, login(client, "student1", "studpass"), login(client, "principal")):
        assert client.get("/driver/routine", headers=bearer(who)).status_code == 403
    driver = login(client, "driver")
    assert _routine(client, driver) == {**_routine(client, driver), "vehicles": [], "shifts": []}
    assert client.get("/driver/routine?date=2026-13-40", headers=bearer(driver)).status_code == 422


def test_vehicle_without_shifts_shows_its_single_pre_0012_shift(client, school):
    admin, driver = school.admin, login(client, "driver")
    van = _vehicle(client, admin, "RJ14 OLD 1", driver["user_id"], shift_name="Morning", shift_start="07:00",
                   shift_end="08:30")
    for student, stop, pickup in ((school.s1, "Stop B", "07:40"), (school.s2, "Stop A", "07:15")):
        _ok(client.put(f"/vehicles/{van}/students/{student}", headers=bearer(admin),
                       json={"stop_name": stop, "pickup_time": pickup, "drop_time": "14:10"}))
    shifts = _routine(client, driver, next_weekday(1))["shifts"]
    assert [(s["key"], s["id"], s["name"], s["trip_type"], s["start_time"]) for s in shifts] == [
        (f"v{van}", None, "Morning", "pickup", "07:00")]
    assert [(r["student_id"], r["stop_no"], r["stop_time"], r["stop_name"]) for r in shifts[0]["students"]] == [
        (school.s2, 1, "07:15", "Stop A"), (school.s1, 2, "07:40", "Stop B")]

    # once the school adds real shifts the old one is gone; the stop name still comes from student_transport
    shift = _shift(client, admin, van, "Morning Shift 1", "pickup", "07:00", "08:00")
    _ok(client.put(f"/vehicles/{van}/shifts/{shift}/students", headers=bearer(admin),
                   json={"students": [{"student_id": school.s1}]}))
    shifts = _routine(client, driver, next_weekday(1))["shifts"]
    assert [s["key"] for s in shifts] == [f"s{shift}"] and shifts[0]["students"][0]["stop_name"] == "Stop B"
    # even with every shift switched off
    _ok(client.put(f"/vehicles/{van}/shifts/{shift}", headers=bearer(admin), json={"is_active": False}))
    assert _routine(client, driver, next_weekday(1))["shifts"] == []


def test_holiday_means_no_shifts(client, school):
    admin, driver = school.admin, login(client, "driver")
    van = _vehicle(client, admin, "RJ14 H 1", driver["user_id"])
    _shift(client, admin, van, "Morning Shift 1", "pickup", "07:00", "08:00", weekdays=[0, 1, 2, 3, 4, 5, 6])
    day = school_today() + timedelta(days=3)
    _ok(client.post("/holidays", headers=bearer(admin), json={"date": str(day), "name": "Diwali"}), 201)
    holiday = _routine(client, driver, day)
    assert (holiday["holiday"], holiday["shifts"]) == ("Diwali", [])
    assert len(_routine(client, driver, day + timedelta(days=1))["shifts"]) == 1


def test_managers_set_up_shifts_and_bad_input_is_refused(client, school):
    admin, driver = school.admin, login(client, "driver")
    van = _vehicle(client, admin, "RJ14 M 1", driver["user_id"])
    van2 = _vehicle(client, admin, "RJ14 M 2", None)
    a = bearer(admin)

    assert client.post(f"/vehicles/{van}/shifts", headers=a, json={
        "name": "x", "trip_type": "pickup", "start_time": "08:00", "end_time": "07:00"}).status_code == 400
    assert client.post(f"/vehicles/{van}/shifts", headers=a, json={
        "name": "x", "trip_type": "bus", "start_time": "07:00", "end_time": "08:00"}).status_code == 422
    assert client.post(f"/vehicles/{van}/shifts", headers=a, json={
        "name": "x", "trip_type": "drop", "start_time": "07:00", "end_time": "08:00",
        "weekdays": [7]}).status_code == 422
    for who in (school.t1, driver):
        assert client.post(f"/vehicles/{van}/shifts", headers=bearer(who), json={
            "name": "x", "trip_type": "pickup", "start_time": "07:00", "end_time": "08:00"}).status_code == 403
    assert client.get(f"/vehicles/{van}/shifts", headers=bearer(driver)).status_code == 403
    assert client.post("/vehicles/999999/shifts", headers=a, json={
        "name": "x", "trip_type": "pickup", "start_time": "07:00", "end_time": "08:00"}).status_code == 404

    shift = _shift(client, admin, van, "Morning Shift 1", "pickup", "07:00", "08:00")
    path = f"/vehicles/{van}/shifts/{shift}"
    assert client.put(path, headers=a, json={"end_time": "06:00"}).status_code == 400
    _ok(client.put(path, headers=a, json={"name": "Morning 1", "end_time": "08:30", "weekdays": [0, 2, 4]}))
    listed = _ok(client.get(f"/vehicles/{van}/shifts", headers=a))
    assert [(s["name"], s["end_time"], s["weekdays"]) for s in listed] == [("Morning 1", "08:30", [0, 2, 4])]

    students = path + "/students"
    assert client.put(students, headers=a, json={"students": [
        {"student_id": school.s1}, {"student_id": school.s1}]}).status_code == 400
    assert client.put(students, headers=a, json={"students": [{"student_id": school.t1_id}]}).status_code == 404
    assert client.put(students, headers=a, json={"students": [
        {"student_id": school.s1, "latitude": 91}]}).status_code == 422
    # a shift is only reachable through its own vehicle
    assert client.put(f"/vehicles/{van2}/shifts/{shift}/students", headers=a,
                      json={"students": []}).status_code == 404
    assert client.delete(f"/vehicles/{van2}/shifts/{shift}", headers=a).status_code == 404

    _ok(client.put(students, headers=a, json={"students": [{"student_id": school.s2}, {"student_id": school.s1}]}))
    _ok(client.put(students, headers=a, json={"students": [{"student_id": school.s1}]}))  # replaces the list
    assert [r["student_id"] for r in _ok(client.get(f"/vehicles/{van}/shifts", headers=a))[0]["students"]] == \
        [school.s1]

    assert client.delete(path, headers=a).status_code == 204
    assert client.delete(path, headers=a).status_code == 404
    assert _ok(client.get(f"/vehicles/{van}/shifts", headers=a)) == []


def test_shifts_never_cross_schools(client, school):
    admin = school.admin
    driver = login(client, "driver")
    van = _vehicle(client, admin, "RJ14 X 1", driver["user_id"])
    shift = _shift(client, admin, van, "Morning Shift 1", "pickup", "07:00", "08:00")

    create_school(client)
    b_admin = _ok(login_to(client, "ABC", "admin", "abc-admin-pass"))
    b = bearer(b_admin)
    b_driver_id = _ok(client.post("/users", headers=b, json={
        "username": "driver", "password": "drivepass", "full_name": "B Driver", "user_type": "driver"}), 201)["id"]
    b_driver = _ok(login_to(client, "ABC", "driver", "drivepass"))
    b_cls = _ok(client.post("/classes", headers=b, json={"name": "Class 1"}), 201)["id"]
    b_sec = _ok(client.post(f"/classes/{b_cls}/sections", headers=b, json={"name": "A"}), 201)["id"]
    b_student = _ok(client.post("/students", headers=b, json={
        "username": "student1", "password": "studpass", "full_name": "B Student", "class_id": b_cls,
        "section_id": b_sec}), 201)["id"]

    assert client.get(f"/vehicles/{van}/shifts", headers=b).status_code == 404
    assert client.post(f"/vehicles/{van}/shifts", headers=b, json={
        "name": "x", "trip_type": "pickup", "start_time": "07:00", "end_time": "08:00"}).status_code == 404
    assert client.put(f"/vehicles/{van}/shifts/{shift}", headers=b, json={"name": "Hijacked"}).status_code == 404
    assert client.delete(f"/vehicles/{van}/shifts/{shift}", headers=b).status_code == 404
    # school A can't put school B's student on its shift
    assert client.put(f"/vehicles/{van}/shifts/{shift}/students", headers=bearer(admin),
                      json={"students": [{"student_id": b_student}]}).status_code == 404

    b_van = _vehicle(client, b_admin, "RJ14 X 1", b_driver_id)
    assert client.get(f"/vehicles/{b_van}/shifts/{shift}", headers=b).status_code == 405  # no such GET
    assert client.put(f"/vehicles/{b_van}/shifts/{shift}", headers=b, json={"name": "x"}).status_code == 404
    assert _routine(client, b_driver, next_weekday(0))["shifts"] == []
    assert [s["key"] for s in _routine(client, driver, next_weekday(0))["shifts"]] == [f"s{shift}"]
