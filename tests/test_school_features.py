"""Leave & substitution, school timings, week timetable, class homework,
notifications and staff-attendance times (BACKEND_README §1–§7)."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest
from helpers import bearer, login
from test_bugfixes import _actor, _run_concurrently

from app.core.clock import school_today
from app.core.errors import Conflict
from app.services import notify, substitutions


def next_weekday(weekday: int, after: date | None = None, weeks: int = 0) -> date:
    """The first `weekday` (0=Mon) strictly after `after` (default today), + weeks."""
    start = (after or school_today()) + timedelta(days=1)
    return start + timedelta(days=(weekday - start.weekday()) % 7 + 7 * weeks)


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json() if r.content else None


@pytest.fixture
def subs(school):
    """t1 teaches Class 1-A Math on Tuesdays in period 3; t2 is busy then; t3
    and t4 are free."""
    c, a = school.client, bearer(school.admin)
    school.t4_id = _ok(c.post("/users", headers=a, json={
        "username": "teacher4", "password": "teachpass", "full_name": "Teacher 4", "user_type": "teacher",
        "teacher_info": {"subject": "Math"}}), 201)["id"]
    school.t4 = login(c, "teacher4", "teachpass")
    for teacher, cls, sec, subject in ((school.t1_id, school.c1, school.c1a, "Math"),
                                       (school.t2_id, school.c2, school.c2a, "Science")):
        _ok(c.put("/timetable", headers=a, json={"teacher_id": teacher, "day": 1, "period": 3,
                                                 "class_id": cls, "section_id": sec, "subject": subject}))
    school.tue = next_weekday(1)
    return school


def ask(school, substitute_id, day=None, period=3, expect=201, who=None, **extra):
    r = school.client.post("/substitutions", headers=bearer(who or school.t1), json={
        "date": str(day or school.tue), "period": period, "substitute_id": substitute_id, **extra})
    assert r.status_code == expect, r.text
    return r.json()


def inbox(client, who):
    return _ok(client.get("/notifications", headers=bearer(who)))


# ------------------------------------------------------------ school timings

def test_school_timings_compute_and_permissions(school):
    c = school.client
    student = login(c, "student1", "studpass")
    assert _ok(c.get("/settings/school-timings", headers=bearer(student))) == {}
    body = {"start_time": "06:30", "prayer_minutes": 30, "period_minutes": 45, "periods_per_day": 8,
            "breaks": [{"after_period": 3, "minutes": 20, "label": "Lunch break"}]}
    assert c.put("/settings/school-timings", headers=bearer(school.t1), json=body).status_code == 403
    coordinator = login(c, "coordinator")
    saved = _ok(c.put("/settings/school-timings", headers=bearer(coordinator), json=body))
    assert saved["periods"][0] == {"period": 1, "start": "07:00", "end": "07:45"}
    assert saved["periods"][3] == {"period": 4, "start": "09:35", "end": "10:20"}
    assert saved["updated_by_name"] == "Coordinator"
    got = _ok(c.get("/settings/school-timings", headers=bearer(student)))
    assert got["start_time"] == "06:30" and got["breaks"][0]["label"] == "Lunch break"

    for bad in ({**body, "start_time": "25:00"}, {**body, "prayer_minutes": 121},
                {**body, "breaks": [{"after_period": 8, "minutes": 10}]},
                {**body, "breaks": [{"after_period": 2, "minutes": 10}, {"after_period": 2, "minutes": 5}]},
                {**body, "start_time": "22:00", "periods_per_day": 12}):
        assert c.put("/settings/school-timings", headers=bearer(coordinator), json=bad).status_code == 422, bad

    a = bearer(school.admin)
    slot = {"teacher_id": school.t1_id, "day": 0, "period": 9, "class_id": school.c1, "section_id": school.c1a}
    assert c.put("/timetable", headers=a, json=slot).status_code == 422
    _ok(c.put("/timetable", headers=a, json={**slot, "period": 8}))
    fewer = {**body, "periods_per_day": 6}
    r = c.put("/settings/school-timings", headers=a, json=fewer)
    assert r.status_code == 409 and "Teacher 1" in r.json()["detail"]
    _ok(c.put("/settings/school-timings?force=true", headers=a, json=fewer))
    assert all(s["period"] <= 6 for s in _ok(c.get("/timetable", headers=a)))


# ------------------------------------------------------------- availability

def test_availability_excludes_busy_teachers(subs):
    c, a = subs.client, bearer(subs.admin)
    wed = subs.tue + timedelta(days=1)
    leave = _ok(c.post("/leaves", headers=bearer(subs.t3), json={
        "from_date": str(wed), "to_date": str(wed), "reason": "x"}), 201)
    assert leave["status"] == "pending"
    _ok(c.post("/holidays", headers=a, json={"date": str(subs.tue + timedelta(days=7)), "name": "Diwali"}), 201)
    _ok(c.put("/timetable", headers=a, json={"teacher_id": subs.t1_id, "day": 2, "period": 1,
                                             "class_id": subs.c1, "section_id": subs.c1a}))
    days = _ok(c.get("/substitutions/availability", headers=bearer(subs.t1),
                     params={"from": str(subs.tue), "to": str(subs.tue + timedelta(days=7))}))["days"]
    by_date = {d["date"]: d for d in days}
    assert all(date.fromisoformat(d).weekday() != 6 for d in by_date)  # no Sundays
    tue = by_date[str(subs.tue)]
    assert [p["period"] for p in tue["periods"]] == [3]
    names = [t["full_name"] for t in tue["periods"][0]["available"]]
    assert names == ["Teacher 4", "Teacher 3"]  # same subject first; t1 (requester) and t2 (busy) excluded
    assert "Teacher 3" not in [t["full_name"] for t in by_date[str(wed)]["periods"][0]["available"]]  # on leave
    holiday = by_date[str(subs.tue + timedelta(days=7))]
    assert holiday["holiday"] == "Diwali" and holiday["periods"] == []

    r = c.get("/substitutions/availability", headers=bearer(subs.t1),
              params={"from": str(subs.tue), "to": str(subs.tue + timedelta(days=31))})
    assert r.status_code == 400

    # t4 accepts to cover t2 in that period, so t4 is no longer free for t1
    ask(subs, subs.t4_id, who=subs.t2)
    rid = _ok(c.get("/substitutions/incoming", headers=bearer(subs.t4)))[0]["id"]
    _ok(c.put(f"/substitutions/{rid}/accept", headers=bearer(subs.t4), json={}))
    tue = _ok(c.get("/substitutions/availability", headers=bearer(subs.t1),
                    params={"from": str(subs.tue), "to": str(subs.tue)}))["days"][0]
    assert [t["full_name"] for t in tue["periods"][0]["available"]] == ["Teacher 3"]


def test_create_rules(subs):
    c = subs.client
    r = ask(subs, subs.t3_id, note="Family function")
    assert r["status"] == "pending" and r["subject"] == "Math" and r["class_name"] == "Class 1"
    assert r["requester_name"] == "Teacher 1" and r["substitute_name"] == "Teacher 3" and r["day"] == 1
    assert ask(subs, subs.t3_id, expect=409)["detail"].startswith("Teacher 3 has already been asked")
    assert ask(subs, subs.t2_id, expect=409)["detail"] == "Teacher 2 is not free in period 3 that day"
    assert "no class in period 4" in ask(subs, subs.t3_id, period=4, expect=400)["detail"]
    assert "in the past" in ask(subs, subs.t3_id, day=subs.tue - timedelta(days=14), expect=400)["detail"]
    ask(subs, subs.t4_id)  # broadcast to a second teacher is fine
    assert ask(subs, subs.t3_id, who=subs.t3, expect=400)  # t3 has no class then

    incoming = _ok(c.get("/notifications", headers=bearer(subs.t3)))
    assert incoming[0]["type"] == "substitution_request"
    assert incoming[0]["title"] == "Teacher 1 asks you to take Period 3"
    assert incoming[0]["data"]["params"] == {"tab": "incoming", "id": r["id"]}
    assert _ok(c.get("/notifications/unread-count", headers=bearer(subs.t3))) == {"count": 1}


def test_accept_fills_others_and_withdraw_frees_the_period(subs):
    c = subs.client
    r3, r4 = ask(subs, subs.t3_id), ask(subs, subs.t4_id)
    assert c.put(f"/substitutions/{r3['id']}/accept", headers=bearer(subs.t1), json={}).status_code == 403
    accepted = _ok(c.put(f"/substitutions/{r3['id']}/accept", headers=bearer(subs.t3), json={}))
    assert accepted["status"] == "accepted" and accepted["can_withdraw"] is True
    assert _ok(c.get(f"/substitutions/{r4['id']}", headers=bearer(subs.t4)))["status"] == "filled"
    assert inbox(c, subs.t4)[0]["title"].startswith("No longer needed: Period 3")
    assert inbox(c, subs.t1)[0]["title"].startswith("Teacher 3 will take Period 3")
    assert ask(subs, subs.t4_id, expect=409)["detail"] == "Period 3 is already covered by Teacher 3"

    week3 = _ok(c.get("/timetable/week", headers=bearer(subs.t3), params={"start": str(subs.tue)}))
    assert week3["start"] == str(subs.tue - timedelta(days=1)) and len(week3["days"]) == 6
    assert [(s["kind"], s["status"], s["other_teacher_name"]) for s in week3["substitutions"]] == [
        ("covering", "accepted", "Teacher 1")]
    week1 = _ok(c.get("/timetable/week", headers=bearer(subs.t1), params={"start": str(subs.tue)}))
    assert [(s["kind"], s["other_teacher_name"]) for s in week1["substitutions"]] == [("covered", "Teacher 3")]
    assert len(week1["slots"]) == 1

    withdrawn = _ok(c.put(f"/substitutions/{r3['id']}/withdraw", headers=bearer(subs.t3),
                          json={"note": "by mistake"}))
    assert withdrawn["status"] == "withdrawn" and withdrawn["response_note"] == "by mistake"
    assert inbox(c, subs.t1)[0]["title"].startswith("Teacher 3 can no longer take Period 3")
    week3 = _ok(c.get("/timetable/week", headers=bearer(subs.t3), params={"start": str(subs.tue)}))
    assert week3["substitutions"] == []
    tue = _ok(c.get("/substitutions/availability", headers=bearer(subs.t1),
                    params={"from": str(subs.tue), "to": str(subs.tue)}))["days"][0]["periods"][0]
    assert "Teacher 3" in [t["full_name"] for t in tue["available"]]
    assert [x["status"] for x in tue["requests"]] == ["filled", "withdrawn"]

    # cancel: the requester; the substitute is told
    again = ask(subs, subs.t3_id)
    _ok(c.put(f"/substitutions/{again['id']}/cancel", headers=bearer(subs.t1), json={}))
    assert inbox(c, subs.t3)[0]["title"].startswith("Request withdrawn: Period 3")
    assert c.put(f"/substitutions/{again['id']}/cancel", headers=bearer(subs.t1), json={}).status_code == 409
    assert _ok(c.get("/substitutions/outgoing", headers=bearer(subs.t1)))[0]["id"] == again["id"]
    assert len(_ok(c.get("/substitutions", headers=bearer(subs.admin), params={"teacher_id": subs.t3_id}))) == 2
    assert c.get("/substitutions", headers=bearer(subs.t1)).status_code == 403
    assert c.get(f"/substitutions/{again['id']}", headers=bearer(subs.t2)).status_code == 404


def test_two_teachers_accept_at_the_same_moment(subs, db):
    r3, r4 = ask(subs, subs.t3_id), ask(subs, subs.t4_id)
    actors = [_actor(db, "teacher3"), _actor(db, "teacher4")]
    ids = [r3["id"], r4["id"]]
    results = _run_concurrently(lambda d, i: substitutions.accept(d, actors[i], ids[i], None), n=2)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, Conflict) for r in results) == 1
    statuses = sorted(r["status"] for r in db.all("SELECT status FROM substitution_requests"))
    assert statuses == ["accepted", "filled"]


def test_can_withdraw_until_the_period_starts():
    today = school_today()
    row = {"status": "accepted", "date": today, "period": 1}
    assert substitutions._can_withdraw(row, {}) is True  # no timings: until the day is over
    assert substitutions._can_withdraw({**row, "date": today - timedelta(days=1)}, {}) is False
    assert substitutions._can_withdraw(row, {1: {"start": "00:00", "end": "00:45"}}) is False
    assert substitutions._can_withdraw({**row, "date": today + timedelta(days=1)},
                                       {1: {"start": "00:00", "end": "00:45"}}) is True
    assert substitutions._can_withdraw({**row, "status": "pending"}, {}) is False


# -------------------------------------------------------------------- leaves

def test_leave_with_substitutions_coverage_and_edits(subs):
    c, a = subs.client, bearer(subs.admin)
    _ok(c.put("/timetable", headers=a, json={"teacher_id": subs.t1_id, "day": 3, "period": 2,
                                             "class_id": subs.c1, "section_id": subs.c1a}))
    tue, wed, thu = subs.tue, subs.tue + timedelta(days=1), subs.tue + timedelta(days=2)
    _ok(c.post("/holidays", headers=a, json={"date": str(wed), "name": "Mid"}), 201)
    standalone = ask(subs, subs.t4_id)  # sent before the leave exists

    bad = c.post("/leaves", headers=bearer(subs.t1), json={
        "from_date": str(tue), "to_date": str(thu), "reason": "Trip",
        "substitutions": [{"date": str(thu), "period": 2, "substitute_id": subs.t3_id},
                          {"date": str(thu), "period": 5, "substitute_id": subs.t3_id}]})
    assert bad.status_code == 409 and "no class in period 5" in bad.json()["detail"]
    assert _ok(c.get("/leaves/mine", headers=bearer(subs.t1))) == []  # nothing was created

    leave_id = _ok(c.post("/leaves", headers=bearer(subs.t1), json={
        "from_date": str(tue), "to_date": str(thu), "reason": "Trip",
        "substitutions": [{"date": str(thu), "period": 2, "substitute_id": subs.t3_id}]}), 201)["id"]
    assert inbox(c, subs.admin)[0]["title"].startswith("Teacher 1 applied for leave")
    _ok(c.put(f"/substitutions/{standalone['id']}/accept", headers=bearer(subs.t4), json={}))
    mine = _ok(c.get("/leaves/mine", headers=bearer(subs.t1)))[0]
    assert mine["user_id"] == subs.t1_id and mine["created_at"]
    assert mine["coverage"] == {"periods": 2, "covered": 1, "pending": 1}  # Wed is a holiday
    detail = _ok(c.get(f"/leaves/{leave_id}", headers=bearer(subs.t1)))
    assert sorted(s["status"] for s in detail["substitutions"]) == ["accepted", "pending"]  # standalone linked
    assert c.get(f"/leaves/{leave_id}", headers=bearer(subs.t2)).status_code == 404
    assert _ok(c.get("/leaves", headers=a))[0]["coverage"]["periods"] == 2

    # shorten: the Thursday request is cancelled and t3 is told
    edited = _ok(c.put(f"/leaves/{leave_id}", headers=bearer(subs.t1), json={
        "from_date": str(tue), "to_date": str(tue), "reason": "Shorter"}))
    assert edited["reason"] == "Shorter"
    assert sorted(s["status"] for s in edited["substitutions"]) == ["accepted", "cancelled"]
    assert inbox(c, subs.t3)[0]["title"] == f"Request withdrawn: Period 2 on {notify.weekday_date(thu)}"

    # reject cancels the rest
    _ok(c.put(f"/leaves/{leave_id}/reject", headers=a, json={"note": "Exams"}))
    assert inbox(c, subs.t1)[0]["title"].endswith("was rejected")
    assert inbox(c, subs.t4)[0]["title"].startswith("Request withdrawn: Period 3")
    assert c.put(f"/leaves/{leave_id}", headers=bearer(subs.t1), json={
        "from_date": str(tue), "to_date": str(tue), "reason": "x"}).status_code == 409


def test_delete_and_cancel_leaves(subs):
    c, a = subs.client, bearer(subs.admin)
    t1 = bearer(subs.t1)
    pending = _ok(c.post("/leaves", headers=t1, json={"from_date": str(subs.tue), "to_date": str(subs.tue),
                                                      "reason": "x"}), 201)["id"]
    req = ask(subs, subs.t3_id, leave_id=pending)
    assert c.delete(f"/leaves/{pending}", headers=bearer(subs.t2)).status_code == 404
    assert c.delete(f"/leaves/{pending}", headers=t1).status_code == 204
    assert _ok(c.get(f"/substitutions/{req['id']}", headers=t1))["status"] == "cancelled"
    assert _ok(c.get("/leaves/mine", headers=t1)) == []

    approved = _ok(c.post("/leaves", headers=t1, json={"from_date": str(subs.tue), "to_date": str(subs.tue),
                                                       "reason": "y"}), 201)["id"]
    _ok(c.put(f"/leaves/{approved}/approve", headers=a, json={}))
    assert inbox(c, subs.t1)[0]["title"].endswith("was approved")
    assert c.delete(f"/leaves/{approved}", headers=t1).status_code == 204
    assert _ok(c.get(f"/leaves/{approved}", headers=t1))["status"] == "cancelled"
    assert "was cancelled" in inbox(c, subs.admin)[0]["title"]

    started = _ok(c.post("/leaves", headers=t1, json={"from_date": str(school_today()),
                                                      "to_date": str(subs.tue), "reason": "z"}), 201)["id"]
    _ok(c.put(f"/leaves/{started}/approve", headers=a, json={}))
    assert c.delete(f"/leaves/{started}", headers=t1).status_code == 409
    assert c.delete(f"/leaves/{started}", headers=a).status_code == 409


# ------------------------------------------------------------------ homework

def test_class_homework(school):
    c = school.client
    t2 = bearer(school.t2)  # subject teacher of Class 1-A
    due = school_today() + timedelta(days=2)
    body = {"class_id": school.c1, "section_id": school.c1a, "title": "Ex 4.2", "subject": "Maths",
            "due_date": str(due)}
    assert c.post("/homework", headers=bearer(school.t3), json=body).status_code == 403
    assert c.post("/homework", headers=t2, json={**body, "due_date": str(school_today() - timedelta(days=1))}
                  ).status_code == 422
    hw = _ok(c.post("/homework", headers=t2, json=body), 201)
    assert hw["counts"] == {"total": 2, "submitted": 0, "pending": 2, "late": 0}
    assert hw["assigned_by"] == "Teacher 2"

    s1 = login(c, "student1", "studpass")
    note = inbox(c, s1)[0]
    assert note["type"] == "homework" and note["title"] == f"📚 New homework: Maths — Ex 4.2 (due {due.day} {due:%b})"
    assert note["data"]["route"] == "Homework"
    assert inbox(c, login(c, "student3", "studpass")) == []  # other section

    _ok(c.post(f"/students/{school.s1}/homework", headers=t2, json={"title": "Old style"}), 201)
    mine = _ok(c.get("/student/homework", headers=bearer(s1)))
    assert sorted((h["scope"], h["title"], h["status"], h["homework_id"]) for h in mine) == [
        ("class", "Ex 4.2", "pending", hw["id"]), ("student", "Old style", "pending", None)]
    assert next(h for h in mine if h["scope"] == "class")["subject"] == "Maths"
    assert _ok(c.get("/student/dashboard", headers=bearer(s1)))["pending_homework"] == 2

    _ok(c.put(f"/homework/{hw['id']}/submissions", headers=t2, json={"items": [
        {"student_id": school.s1, "status": "submitted"}, {"student_id": school.s2, "status": "late"}]}), 204)
    assert c.put(f"/homework/{hw['id']}/submissions", headers=t2, json={"items": [
        {"student_id": school.s4, "status": "submitted"}]}).status_code == 400  # not in the section
    subs_list = _ok(c.get(f"/homework/{hw['id']}/submissions", headers=t2))
    assert [(s["student_id"], s["status"]) for s in subs_list] == [(school.s1, "submitted"), (school.s2, "late")]

    # a student who joins later sees it as pending
    new = _ok(c.post("/students", headers=bearer(school.admin), json={
        "username": "student9", "password": "studpass", "full_name": "Student 9", "roll_no": "9",
        "class_id": school.c1, "section_id": school.c1a}), 201)["id"]
    listed = _ok(c.get("/homework", headers=t2, params={"class_id": school.c1, "section_id": school.c1a}))
    assert listed[0]["counts"] == {"total": 3, "submitted": 1, "pending": 1, "late": 1}
    staff_view = _ok(c.get(f"/students/{new}/homework", headers=t2))
    assert [(h["scope"], h["id"], h["homework_id"], h["status"]) for h in staff_view] == [
        ("class", hw["id"], hw["id"], "pending")]
    _ok(c.put(f"/students/{new}/homework/{hw['id']}?scope=class", headers=t2, json={"status": "submitted"}))
    assert _ok(c.get(f"/students/{new}/homework", headers=t2))[0]["status"] == "submitted"
    assert c.delete(f"/students/{new}/homework/{hw['id']}?scope=class", headers=t2).status_code == 409

    # edit: only the creator or managers; a due-date change notifies the students
    t1 = bearer(school.t1)
    assert c.put(f"/homework/{hw['id']}", headers=t1, json={"title": "Changed"}).status_code == 403
    later = due + timedelta(days=3)
    updated = _ok(c.put(f"/homework/{hw['id']}", headers=t2, json={**body, "due_date": str(later)}))
    assert updated["due_date"] == str(later)
    assert inbox(c, s1)[0]["title"].startswith("📚 Homework due date changed")
    assert c.put(f"/homework/{hw['id']}", headers=t2, json={**body, "section_id": school.c1b}).status_code == 400
    assert c.get("/homework", headers=bearer(school.t3), params={"class_id": school.c1,
                                                                 "section_id": school.c1a}).status_code == 403
    assert c.delete(f"/homework/{hw['id']}", headers=t2).status_code == 204
    assert [h["scope"] for h in _ok(c.get("/student/homework", headers=bearer(s1)))] == ["student"]


# ------------------------------------------------------------- notifications

def test_attendance_notifies_only_changes(school):
    c, t1 = school.client, bearer(school.t1)
    today = str(school_today())
    mark = {"section_id": school.c1a, "date": today, "records": [
        {"student_id": school.s1, "status": "present"}, {"student_id": school.s2, "status": "absent"}]}
    _ok(c.post("/attendance", headers=t1, json=mark))
    s1, s2 = login(c, "student1", "studpass"), login(c, "student2", "studpass")
    assert inbox(c, s1)[0]["title"] == "✅ Student is present today"
    assert inbox(c, s2)[0]["title"].startswith("⚠️ Student was marked absent today")
    mark["records"][0]["status"] = "absent"
    _ok(c.post("/attendance", headers=t1, json=mark))
    assert [n["title"] for n in inbox(c, s1)][0] == "Attendance updated: Student is now absent"
    assert len(inbox(c, s2)) == 1  # unchanged -> nothing new

    first = inbox(c, s1)[-1]["id"]
    assert c.put(f"/notifications/{first}/read", headers=bearer(s2)).status_code == 404
    assert c.put(f"/notifications/{first}/read", headers=bearer(s1)).status_code == 204
    assert _ok(c.get("/notifications/unread-count", headers=bearer(s1))) == {"count": 1}
    assert c.put("/notifications/read-all", headers=bearer(s1)).status_code == 204
    assert _ok(c.get("/notifications/unread-count", headers=bearer(s1))) == {"count": 0}
    r = c.get("/notifications", headers=bearer(s1), params={"limit": 1})
    assert r.headers["X-Total-Count"] == "2" and len(r.json()) == 1


def test_fee_and_notice_notifications(school):
    c, a = school.client, bearer(school.admin)
    fee = _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Term 2", "amount": 125000,
                                               "due_date": "2030-06-30"}), 201)["id"]
    s1 = login(c, "student1", "studpass")
    assert inbox(c, s1)[0]["title"] == "New fee: Term 2 — ₹1,25,000 due 30 Jun"
    assert inbox(c, s1)[0]["data"] == {"route": "Fees", "web_path": "/fees"}
    _ok(c.put(f"/fees/{fee}/pay", headers=a, json={"method": "cash"}))
    assert inbox(c, s1)[0]["title"] == "Payment received: Term 2 ₹1,25,000"
    _ok(c.post("/fees/generate", headers=a, json={"class_id": school.c2, "title": "Bus"}), 201)
    assert inbox(c, login(c, "student4", "studpass"))[0]["title"] == "New fee: Bus — ₹6,000"

    principal = login(c, "principal")
    _ok(c.post("/notices", headers=bearer(principal), json={"title": "Sports day", "body": "x" * 300}), 201)
    staff_note = inbox(c, school.t1)[0]
    assert staff_note["title"] == "📢 Sports day" and len(staff_note["body"]) == 120
    assert staff_note["data"]["route"] == "StaffNotices"
    assert inbox(c, s1)[0]["data"]["route"] == "Notices"
    assert inbox(c, principal) == []


def test_fee_reminder_job(school, monkeypatch, db):
    from app.routes import jobs

    c, a = school.client, bearer(school.admin)
    assert c.post("/internal/jobs/fee-reminders").status_code == 404  # JOBS_SECRET not set
    monkeypatch.setattr(jobs, "settings", dataclasses.replace(jobs.settings, jobs_secret="s3cret"))
    assert c.post("/internal/jobs/fee-reminders", headers={"X-Jobs-Secret": "nope"}).status_code == 403
    today = school_today()
    _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Soon", "amount": 100,
                                         "due_date": str(today + timedelta(days=2))}), 201)
    late = _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Late", "amount": 100}), 201)
    db.execute("UPDATE fees SET due_date = %s WHERE id = %s", (today - timedelta(days=1), late["id"]))
    run = lambda: _ok(c.post("/internal/jobs/fee-reminders", headers={"X-Jobs-Secret": "s3cret"}))  # noqa: E731
    assert run() == {"due_soon": 1, "overdue": 1}
    assert run() == {"due_soon": 0, "overdue": 0}  # never twice
    titles = [n["title"] for n in inbox(c, login(c, "student1", "studpass"))]
    assert any(t.startswith("Fee overdue: Late") for t in titles)
    assert any(t.startswith("Fee due soon: Soon") for t in titles)


def test_devices_and_push(school, monkeypatch, db):
    messaging = pytest.importorskip("firebase_admin.messaging")
    c = school.client
    s1 = login(c, "student1", "studpass")
    _ok(c.post("/devices", headers=bearer(s1), json={"token": "tok-good", "platform": "android"}), 204)
    _ok(c.post("/devices", headers=bearer(s1), json={"token": "tok-gone", "platform": "ios"}), 204)
    assert c.post("/devices", headers=bearer(s1), json={"token": "x", "platform": "pager"}).status_code == 422

    sent = []

    class Response:
        def __init__(self, ok, exc=None):
            self.success, self.exception = ok, exc

    def send_each(messages, app=None):
        sent.append(messages)
        return type("Batch", (), {"responses": [
            Response(m.token != "tok-gone", None if m.token != "tok-gone" else messaging.UnregisteredError("gone"))
            for m in messages]})()

    monkeypatch.setattr(notify, "push_configured", lambda: True)
    monkeypatch.setattr(notify, "_firebase_app", lambda: object())
    monkeypatch.setattr(messaging, "send_each", send_each)
    _ok(c.post("/fees", headers=bearer(school.admin), json={"student_id": school.s1, "title": "T", "amount": 5}),
        201)
    assert len(sent) == 1 and {m.token for m in sent[0]} == {"tok-good", "tok-gone"}
    msg = sent[0][0]
    assert msg.data["type"] == "fee" and msg.data["route"] == "Fees" and msg.data["params"] == "{}"
    assert msg.android.priority == "high" and msg.android.notification.channel_id == "default"
    assert [r["token"] for r in db.all("SELECT token FROM device_tokens")] == ["tok-good"]

    # a failed action sends nothing
    sent.clear()
    assert c.post("/fees", headers=bearer(school.admin), json={"student_id": 999999, "title": "T",
                                                               "amount": 5}).status_code == 404
    assert sent == []

    # sign-out: DELETE without auth; logout removes this login's devices
    assert c.delete("/devices/tok-good").status_code == 204
    _ok(c.post("/devices", headers=bearer(s1), json={"token": "tok-2", "platform": "android"}), 204)
    _ok(c.post("/auth/logout", headers=bearer(s1)))
    assert db.all("SELECT token FROM device_tokens") == []


def test_notifications_without_firebase_still_work(school):
    c = school.client
    assert not notify.push_configured()
    _ok(c.post("/fees", headers=bearer(school.admin), json={"student_id": school.s1, "title": "T",
                                                           "amount": 5}), 201)
    assert len(inbox(c, login(c, "student1", "studpass"))) == 1


def test_text_helpers():
    assert notify.inr(12500) == "₹12,500"
    assert notify.inr("1250000.50") == "₹12,50,000.50"
    assert notify.inr(950) == "₹950"
    assert notify.date_range(date(2026, 5, 12), date(2026, 5, 14)) == "12–14 May"
    assert notify.date_range(date(2026, 4, 30), date(2026, 5, 2)) == "30 Apr–2 May"
    assert notify.weekday_date(date(2026, 5, 12)) == "Tue 12 May"


# ---------------------------------------------------------- staff attendance

def test_staff_attendance_times_are_clock_strings(school):
    c, t1 = school.client, bearer(school.t1)
    _ok(c.post("/staff-attendance/check-in", headers=t1))
    today = _ok(c.get("/staff-attendance/today", headers=t1))
    assert isinstance(today["in_time"], str) and len(today["in_time"]) == 8 and today["in_time"][2] == ":"
    assert today["out_time"] is None
    assert _ok(c.get("/staff-attendance/mine", headers=t1))[0]["in_time"] == today["in_time"]
    r = c.get("/staff-attendance", headers=bearer(school.admin), params={"limit": 10})
    assert r.headers["X-Total-Count"] == "1" and r.json()[0]["in_time"] == today["in_time"]
