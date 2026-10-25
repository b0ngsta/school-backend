"""GET /updates — newest content time per home-screen section (the app's red dots)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from helpers import bearer, login

from app.core.clock import school_today
from app.services.updates import STUDENT_SECTIONS, staff_sections


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def _sections(client, who) -> dict:
    body = _ok(client.get("/updates", headers=bearer(who)))
    assert datetime.fromisoformat(body["server_time"]).utcoffset() == timedelta(hours=5, minutes=30)
    return body["sections"]


def _latest(sections: dict, key: str) -> datetime | None:
    value = sections[key]["latest_at"]
    return datetime.fromisoformat(value) if value else None


def _next_weekday(start: date, weekday: int) -> date:
    return start + timedelta(days=(weekday - start.weekday()) % 7 or 7)


def test_student_sections_follow_their_class(client, school):
    s1, s4 = login(client, "student1", "studpass"), login(client, "student4", "studpass")
    empty = _sections(client, s1)
    assert set(empty) == set(STUDENT_SECTIONS)
    assert all(v["latest_at"] is None for v in empty.values())
    assert empty["messages"]["unread"] == 0

    t1, a = bearer(school.t1), bearer(school.admin)
    _ok(client.post("/homework", headers=t1, json={"class_id": school.c1, "section_id": school.c1a,
                                                   "title": "Read chapter 3"}), 201)
    _ok(client.post("/notices", headers=a, json={"title": "PTM", "body": "Saturday"}), 201)
    _ok(client.post("/messages", headers=t1, json={"title": "Hi", "body": "Bring books",
                                                   "student_ids": [school.s1]}), 201)
    _ok(client.post("/attendance", headers=t1, json={
        "section_id": school.c1a, "date": str(school_today()),
        "records": [{"student_id": school.s1, "status": "present"}]}))

    mine = _sections(client, s1)
    for key in ("homework", "notices", "messages", "attendance"):
        assert _latest(mine, key) is not None, key
        assert _latest(mine, key).utcoffset() == timedelta(hours=5, minutes=30)
    assert mine["messages"]["unread"] == 1
    assert _latest(mine, "events") is None

    # Class 2-A: the school-wide notice, not 1-A's homework / message / attendance
    other = _sections(client, s4)
    assert _latest(other, "notices") is not None
    for key in ("homework", "messages", "attendance"):
        assert _latest(other, key) is None, key

    # opening the message is read state only; the newest time stays
    msg = _ok(client.get("/student/messages", headers=bearer(s1)))[0]
    _ok(client.get(f"/student/messages/{msg['id']}", headers=bearer(s1)))
    after = _sections(client, s1)
    assert after["messages"]["unread"] == 0 and after["messages"]["latest_at"] == mine["messages"]["latest_at"]


def test_staff_sections_by_role(client, school):
    teacher = _sections(client, school.t1)
    assert set(teacher) == set(staff_sections("teacher"))
    assert set(_sections(client, school.admin)) == set(staff_sections("admin"))
    driver = login(client, "driver")
    assert set(_ok(client.get("/updates", headers=bearer(driver)))["sections"]) == set(staff_sections("driver"))

    a, t1 = bearer(school.admin), bearer(school.t1)
    _ok(client.post("/notices", headers=a, json={"title": "Staff meeting", "body": "4 pm"}), 201)
    # a new notice is news to the teacher, not to the manager who posted it
    assert _latest(_sections(client, school.t1), "notices") is not None
    assert _latest(_sections(client, school.admin), "notices") is None

    day = _next_weekday(school_today(), 1)
    leave = _ok(client.post("/leaves", headers=t1, json={"from_date": str(day), "to_date": str(day),
                                                         "reason": "Wedding"}), 201)["id"]
    # managers: someone asked for leave · the teacher: nothing yet (not answered)
    assert _latest(_sections(client, school.admin), "leaves") is not None
    assert _latest(_sections(client, school.t1), "leaves") is None
    _ok(client.put(f"/leaves/{leave}/approve", headers=a, json={}))
    assert _latest(_sections(client, school.t1), "leaves") is not None
    assert _latest(_sections(client, school.t2), "leaves") is None


def test_updates_need_sign_in(client):
    assert client.get("/updates").status_code == 401
