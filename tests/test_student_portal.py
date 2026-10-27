"""Student-portal features: school profile, launch announcements, attendance
overview, homework by date / subject with attachments, documents
(timetable / syllabus), notices with attachments, messages, events,
transport and subject teachers."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from helpers import PDF_BYTES, bearer, login, png_bytes

from app.core.clock import school_today
from app.services import school_profile, student_attendance


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def _pdf(name="file.pdf"):
    return {"file": (name, PDF_BYTES, "application/pdf")}


def _png(name="photo.png"):
    return {"file": (name, png_bytes(), "image/png")}


@pytest.fixture
def s1(client, school):
    return login(client, "student1", "studpass")


@pytest.fixture
def s4(client, school):
    return login(client, "student4", "studpass")


# ------------------------------------------------------------ school profile

def test_session_bounds():
    assert school_profile.session_bounds(4, date(2026, 10, 25)) == (date(2026, 4, 1), date(2027, 3, 31))
    assert school_profile.session_bounds(4, date(2027, 2, 1)) == (date(2026, 4, 1), date(2027, 3, 31))
    assert school_profile.session_bounds(1, date(2026, 5, 5)) == (date(2026, 1, 1), date(2026, 12, 31))
    assert school_profile.session_bounds(6, date(2026, 5, 31)) == (date(2025, 6, 1), date(2026, 5, 31))
    assert school_profile.session_label(date(2026, 4, 1), date(2027, 3, 31)) == "2026-2027"


def test_school_profile_read_by_everyone_edited_by_admin(client, school, s1):
    a, t = bearer(school.admin), bearer(school.t1)
    profile = _ok(client.put("/school", headers=a, json={
        "director_name": "Rajveer Singh", "affiliation_no": "1730786", "phone": "9950367365",
        "email": "office@school.example", "website": "www.school.example", "address": "Ratangarh Road"}))
    assert profile["director_name"] == "Rajveer Singh"
    assert profile["session"]["label"].count("-") == 1
    assert _ok(client.put("/school", headers=t, json={"director_name": "x"}), 403)
    assert _ok(client.put("/school", headers=a, json={"email": "not-an-email"}), 422)

    seen = _ok(client.get("/school", headers=bearer(s1)))
    assert (seen["code"], seen["affiliation_no"], seen["phone"]) == ("DEFAULT", "1730786", "9950367365")
    # blank clears, left-out keeps
    profile = _ok(client.put("/school", headers=a, json={"phone": ""}))
    assert profile["phone"] is None and profile["director_name"] == "Rajveer Singh"

    logo = _ok(client.post("/school/logo", headers=a, files=_png()))
    assert "sig=" in logo["logo_path"]
    assert _ok(client.post("/school/logo", headers=a, files=_pdf()), 415)
    assert "sig=" in _ok(client.get("/school", headers=bearer(s1)))["logo_path"]
    _ok(client.delete("/school/logo", headers=a), 204)
    assert _ok(client.get("/school", headers=bearer(s1)))["logo_path"] is None


def test_principal_name_falls_back_to_the_principal_account(client, school):
    assert _ok(client.get("/school", headers=bearer(school.admin)))["principal_name"] == "Principal"


# -------------------------------------------------------------- announcements

def test_announcements_popup(client, school, s1):
    a, s = bearer(school.admin), bearer(s1)
    today = school_today()
    live = _ok(client.post("/announcements", headers=a, json={
        "title": "School timing changed", "body": "From Monday school starts at 8:00"}), 201)
    _ok(client.post("/announcements", headers=a, json={
        "title": "Later", "starts_on": str(today + timedelta(days=3))}), 201)
    _ok(client.post("/announcements", headers=a, json={
        "title": "Over", "starts_on": str(today - timedelta(days=9)), "ends_on": str(today - timedelta(days=1))}), 201)
    _ok(client.post("/announcements", headers=a, json={"title": "Off", "is_active": False}), 201)
    assert _ok(client.post("/announcements", headers=a, json={
        "title": "Bad", "starts_on": str(today), "ends_on": str(today - timedelta(days=1))}), 422)

    for _ in range(5):
        _ok(client.post(f"/announcements/{live['id']}/images", headers=a, files=_png()), 201)
    assert _ok(client.post(f"/announcements/{live['id']}/images", headers=a, files=_png()), 400)

    shown = _ok(client.get("/student/announcements", headers=s))
    assert [x["title"] for x in shown] == ["School timing changed"]
    assert len(shown[0]["images"]) == 5 and all("sig=" in i["image_path"] for i in shown[0]["images"])

    assert _ok(client.get("/announcements", headers=bearer(school.t1)), 403)
    assert _ok(client.post("/announcements", headers=bearer(school.t1), json={"title": "x"}), 403)
    assert len(_ok(client.get("/announcements", headers=a))) == 4

    image_id = shown[0]["images"][0]["id"]
    _ok(client.delete(f"/announcements/{live['id']}/images/{image_id}", headers=a), 204)
    _ok(client.put(f"/announcements/{live['id']}", headers=a, json={"title": "Changed", "is_active": False}))
    assert _ok(client.get("/student/announcements", headers=s)) == []
    _ok(client.delete(f"/announcements/{live['id']}", headers=a), 204)
    assert _ok(client.delete(f"/announcements/{live['id']}", headers=a), 404)


# ----------------------------------------------------------------- attendance

def test_attendance_month_counts():
    holidays = {date(2026, 10, 11): "Navratri", date(2026, 10, 19): "Durgashtami", date(2026, 10, 20): "Dussehra"}
    records = {date(2026, 10, d): "present" for d in range(1, 17)}
    records.update({date(2026, 10, 7): "absent", date(2026, 10, 10): "absent", date(2026, 10, 22): "absent"})
    # the whole month is over: every working day counts
    month = student_attendance._month_counts(2026, 10, holidays, records, today=date(2026, 11, 2))
    assert (month["days"], month["sundays"], month["holidays"], month["working_days"]) == (31, 4, 2, 25)
    assert (month["present"], month["absent"]) == (14, 3)
    assert month["percent"] == 56
    # on 17 Oct only the days so far count (1-16 Oct minus 2 Sundays); days to come can't be missed
    records.pop(date(2026, 10, 22))
    month = student_attendance._month_counts(2026, 10, holidays, records, today=date(2026, 10, 17))
    assert (month["working_days"], month["working_days_total"]) == (14, 25)
    assert month["percent"] == 100
    # today counts once it has been marked
    records[date(2026, 10, 17)] = "present"
    assert student_attendance._month_counts(2026, 10, holidays, records, today=date(2026, 10, 17))[
        "working_days"] == 15


def test_attendance_half_day_overview_and_month(client, school, s1):
    t1 = bearer(school.t1)
    today = school_today()
    days = [today - timedelta(days=n) for n in range(0, 10) if (today - timedelta(days=n)).weekday() != 6][:3]
    statuses = ["present", "half_day", "absent"]
    for day, status in zip(days, statuses, strict=True):
        _ok(client.post("/attendance", headers=t1, json={
            "section_id": school.c1a, "date": str(day), "records": [{"student_id": school.s1, "status": status}]}))
    assert _ok(client.post("/attendance", headers=t1, json={
        "section_id": school.c1a, "date": str(today), "records": [{"student_id": school.s1, "status": "late"}]}), 422)
    rows = _ok(client.get(f"/attendance?section_id={school.c1a}&day={days[1]}", headers=t1))
    assert {r["student_id"]: r["status"] for r in rows}[school.s1] == "half_day"

    ov = _ok(client.get("/student/attendance/overview", headers=bearer(s1)))
    assert (ov["totals"]["present"], ov["totals"]["half_day"], ov["totals"]["absent"]) == (1, 1, 1)
    assert ov["totals"]["working_days_total"] > 250  # the whole session
    assert 3 <= ov["totals"]["working_days"] < ov["totals"]["working_days_total"]  # only days so far
    assert ov["months"][-1]["month"] == today.month
    assert ov["totals"]["percent"] == round(1.5 * 100 / ov["totals"]["working_days"], 1)

    m = _ok(client.get(f"/student/attendance/month?year={days[0].year}&month={days[0].month}", headers=bearer(s1)))
    by_day = {d["date"]: d["status"] for d in m["days"]}
    assert by_day[str(days[0])] == "present"
    assert not any(d["is_future"] for d in m["days"] if d["date"] <= str(today))
    assert sum(m["summary"].values()) <= len(m["days"])
    assert _ok(client.get("/student/attendance/month?year=2026&month=13", headers=bearer(s1)), 400)
    notes = _ok(client.get("/notifications", headers=bearer(s1)))
    assert any("half the day" in n["title"] for n in notes)


# ------------------------------------------------------------------- homework

def test_homework_attachment_subjects_and_filters(client, school, s1, s4):
    a, t1, t2, s = bearer(school.admin), bearer(school.t1), bearer(school.t2), bearer(s1)
    _ok(client.put(f"/classes/{school.c1}/subjects", headers=a, json={"subjects": ["Hindi", "Math"]}))
    hw = _ok(client.post("/homework", headers=t2, json={
        "class_id": school.c1, "section_id": school.c1a, "title": "Summer homework", "subject": "hindi"}), 201)
    _ok(client.post("/homework", headers=t1, json={"class_id": school.c1, "section_id": school.c1a,
                                                   "title": "Read chapter 2"}), 201)
    # only the teacher who set it (or managers) may attach
    assert _ok(client.post(f"/homework/{hw['id']}/attachment", headers=t1, files=_pdf()), 403)
    done = _ok(client.post(f"/homework/{hw['id']}/attachment", headers=t2, files=_pdf("summer.pdf")))
    assert (done["attachment_type"], done["attachment_name"]) == ("pdf", "summer.pdf")
    assert _ok(client.post(f"/homework/{hw['id']}/attachment", headers=t2,
                           files={"file": ("x.txt", b"hello", "text/plain")}), 415)

    subjects = {x["subject"]: x for x in _ok(client.get("/student/homework/subjects", headers=s))}
    assert list(subjects) == ["Hindi", "Math", "General"]
    assert subjects["Hindi"]["count"] == 1 and subjects["Hindi"]["last_given"]
    assert subjects["Math"]["count"] == 0 and subjects["Math"]["last_given"] is None

    hindi = _ok(client.get("/student/homework?subject=HINDI", headers=s))
    assert [h["title"] for h in hindi] == ["Summer homework"]
    assert "sig=" in hindi[0]["attachment_path"] and hindi[0]["attachment_type"] == "pdf"
    assert [h["title"] for h in _ok(client.get("/student/homework?subject=General", headers=s))] == ["Read chapter 2"]
    assert len(_ok(client.get(f"/student/homework?date={school_today()}", headers=s))) == 2
    assert _ok(client.get(f"/student/homework?date={school_today() - timedelta(days=1)}", headers=s)) == []
    r = client.get("/student/homework?subject=Hindi&limit=1", headers=s)
    assert r.headers["X-Total-Count"] == "1"
    assert _ok(client.get("/student/homework?subject=Hindi", headers=bearer(s4))) == []

    _ok(client.delete(f"/homework/{hw['id']}/attachment", headers=t2), 204)
    assert _ok(client.get("/student/homework?subject=Hindi", headers=s))[0]["attachment_path"] is None
    _ok(client.delete(f"/homework/{hw['id']}", headers=t2), 204)


# ------------------------------------------------------------------ documents

def test_timetable_and_syllabus_documents(client, school, s1, s4):
    a, t1, t2, t3 = bearer(school.admin), bearer(school.t1), bearer(school.t2), bearer(school.t3)
    _ok(client.put(f"/classes/{school.c1}/subjects", headers=a, json={"subjects": ["Hindi", "Math"]}))

    school_wide = _ok(client.post("/documents", headers=a, data={"kind": "timetable", "title": "Half yearly"},
                                  files=_pdf("datesheet.pdf")), 201)
    assert school_wide["class_id"] is None and school_wide["attachment_type"] == "pdf"
    # class teacher of 1-A may share a timetable for 1-A, not for 1-B; subject teacher can't
    _ok(client.post("/documents", headers=t1, data={"kind": "timetable", "title": "PA-1",
                                                    "section_id": school.c1a}, files=_png()), 201)
    assert _ok(client.post("/documents", headers=t1, data={"kind": "timetable", "title": "x",
                                                           "section_id": school.c1b}, files=_pdf()), 403)
    assert _ok(client.post("/documents", headers=t2, data={"kind": "timetable", "title": "x",
                                                           "section_id": school.c1a}, files=_pdf()), 403)
    assert _ok(client.post("/documents", headers=t1, data={"kind": "timetable", "title": "x"}, files=_pdf()), 400)
    # syllabus: any teacher of the class, subject required
    syl = _ok(client.post("/documents", headers=t2, data={"kind": "syllabus", "title": "Maths syllabus",
                                                          "class_id": school.c1, "subject": "Math"},
                          files=_pdf()), 201)
    assert _ok(client.post("/documents", headers=t2, data={"kind": "syllabus", "title": "x",
                                                           "class_id": school.c1}, files=_pdf()), 400)
    assert _ok(client.post("/documents", headers=t3, data={"kind": "syllabus", "title": "x", "class_id": school.c1,
                                                           "subject": "Math"}, files=_pdf()), 403)
    assert _ok(client.post("/documents", headers=a, data={"kind": "notes", "title": "x"}, files=_pdf()), 400)
    assert _ok(client.post("/documents", headers=a, data={"kind": "syllabus", "title": "x", "subject": "Math",
                                                          "class_id": school.c1, "section_id": school.c2a},
                           files=_pdf()), 404)

    s, s4h = bearer(s1), bearer(s4)
    assert [d["title"] for d in _ok(client.get("/student/documents?kind=timetable", headers=s))] == \
        ["PA-1", "Half yearly"]
    assert [d["title"] for d in _ok(client.get("/student/documents?kind=timetable", headers=s4h))] == \
        ["Half yearly"]
    syllabus = _ok(client.get("/student/syllabus", headers=s))
    assert [(g["subject"], len(g["documents"])) for g in syllabus] == [("Hindi", 0), ("Math", 1)]
    assert all(g["documents"] == [] for g in _ok(client.get("/student/syllabus", headers=s4h)))

    # teacher lists: school-wide + own classes only
    assert {d["title"] for d in _ok(client.get("/documents", headers=t3))} == {"Half yearly"}
    # edits: uploader or managers
    assert _ok(client.put(f"/documents/{syl['id']}", headers=t1, data={"title": "x"}), 403)
    changed = _ok(client.put(f"/documents/{syl['id']}", headers=t2, data={"title": "Maths 2026"}, files=_png()))
    assert (changed["title"], changed["attachment_type"]) == ("Maths 2026", "image")
    _ok(client.delete(f"/documents/{syl['id']}", headers=a), 204)
    assert _ok(client.delete(f"/documents/{syl['id']}", headers=a), 404)


# -------------------------------------------------------------------- notices

def test_notices_by_any_manager_with_attachment(client, school, s1):
    co = login(client, "coordinator")
    created = _ok(client.post("/notices", headers=bearer(co), json={"title": "Time table", "body": "Half yearly"}),
                  201)
    assert _ok(client.post("/notices", headers=bearer(school.t1), json={"title": "x", "body": "y"}), 403)
    att = _ok(client.post(f"/notices/{created['id']}/attachment", headers=bearer(co), files=_pdf("tt.pdf")))
    assert att["attachment_name"] == "tt.pdf"
    s = bearer(s1)
    listed = _ok(client.get("/student/notices", headers=s))
    assert listed[0]["attachment_type"] == "pdf" and "sig=" in listed[0]["attachment_path"]
    one = _ok(client.get(f"/student/notices/{created['id']}", headers=s))
    assert one["body"] == "Half yearly"
    assert _ok(client.get("/student/notices/999999", headers=s), 404)
    _ok(client.delete(f"/notices/{created['id']}/attachment", headers=bearer(co)), 204)
    assert _ok(client.get(f"/student/notices/{created['id']}", headers=s))["attachment_path"] is None


# ------------------------------------------------------------------- messages

def test_messages_permissions_targets_and_read_state(client, school, s1):
    a, t1, t2 = bearer(school.admin), bearer(school.t1), bearer(school.t2)
    # class teacher of 1-A: own students / own section only
    one = _ok(client.post("/messages", headers=t1, json={"title": "PTM", "body": "PTM on 27-8",
                                                         "student_ids": [school.s1]}), 201)
    assert (one["audience"], one["audience_label"], one["recipients_count"]) == ("students", "Student 1", 1)
    sec = _ok(client.post("/messages", headers=t1, json={"title": "Timetable", "body": "New timetable",
                                                         "section_id": school.c1a}), 201)
    assert sec["recipients_count"] == 2 and sec["audience_label"] == "Class 1 — A"
    assert _ok(client.post("/messages", headers=t1, json={"title": "x", "body": "y",
                                                          "student_ids": [school.s3]}), 403)
    assert _ok(client.post("/messages", headers=t1, json={"title": "x", "body": "y", "class_id": school.c1}), 403)
    assert _ok(client.post("/messages", headers=t2, json={"title": "x", "body": "y",
                                                          "student_ids": [school.s1]}), 403)
    assert _ok(client.post("/messages", headers=a, json={"title": "x", "body": "y"}), 422)
    assert _ok(client.post("/messages", headers=a, json={"title": "x", "body": "y", "student_ids": [999999]}), 404)
    everyone = _ok(client.post("/messages", headers=bearer(login(client, "principal")),
                               json={"title": "Holiday", "body": "Closed", "whole_school": True}), 201)
    assert everyone["recipients_count"] == 4

    _ok(client.post(f"/messages/{one['id']}/attachment", headers=t1, files=_pdf()))
    s = bearer(s1)
    inbox = _ok(client.get("/student/messages", headers=s))
    assert [m["title"] for m in inbox] == ["Holiday", "Timetable", "PTM"]
    assert _ok(client.get("/student/messages/unread-count", headers=s)) == {"count": 3}
    opened = _ok(client.get(f"/student/messages/{one['id']}", headers=s))
    assert opened["read_at"] and opened["attachment_type"] == "pdf" and opened["sender_name"] == "Teacher 1"
    assert _ok(client.get("/student/messages/unread-count", headers=s)) == {"count": 2}
    # student 3 never got the section message
    s3 = bearer(login(client, "student3", "studpass"))
    assert _ok(client.get(f"/student/messages/{sec['id']}", headers=s3), 404)

    # senders see theirs; managers see all
    assert {m["id"] for m in _ok(client.get("/messages", headers=t1))} == {one["id"], sec["id"]}
    assert len(_ok(client.get("/messages", headers=a))) == 3
    recips = _ok(client.get(f"/messages/{one['id']}/recipients", headers=t1))
    assert recips[0]["read_at"] is not None
    assert _ok(client.get(f"/messages/{everyone['id']}", headers=t1), 404)
    assert _ok(client.delete(f"/messages/{everyone['id']}", headers=t1), 404)
    _ok(client.delete(f"/messages/{one['id']}", headers=t1), 204)


# --------------------------------------------------------------------- events

def test_events_upcoming_completed_and_media(client, school, s1):
    a, s = bearer(school.admin), bearer(s1)
    today = school_today()
    up = _ok(client.post("/events", headers=a, json={
        "title": "Annual Day", "start_date": str(today + timedelta(days=5)), "start_time": "10:00",
        "venue": "Hall"}), 201)
    assert (up["status"], up["end_date"], up["start_time"]) == ("upcoming", str(today + timedelta(days=5)), "10:00")
    past = _ok(client.post("/events", headers=a, json={
        "title": "Sports Day", "start_date": str(today - timedelta(days=3)),
        "end_date": str(today - timedelta(days=2))}), 201)
    assert _ok(client.post("/events", headers=a, json={
        "title": "x", "start_date": str(today), "end_date": str(today - timedelta(days=1))}), 422)
    assert _ok(client.post("/events", headers=bearer(school.t1), json={"title": "x", "start_date": str(today)}), 403)

    _ok(client.post(f"/events/{past['id']}/photos", headers=a, files=_png(), data={"caption": "Race"}), 201)
    _ok(client.post(f"/events/{past['id']}/videos", headers=a, json={"url": "https://youtu.be/abc"}), 201)
    assert _ok(client.post(f"/events/{past['id']}/videos", headers=a, json={"url": "javascript:alert(1)"}), 422)

    assert [e["title"] for e in _ok(client.get("/events?status=upcoming", headers=s))] == ["Annual Day"]
    done = _ok(client.get("/events?status=completed", headers=s))
    assert [e["title"] for e in done] == ["Sports Day"]
    kinds = sorted(m["kind"] for m in done[0]["media"])
    assert kinds == ["image", "video"]
    assert "sig=" in next(m for m in done[0]["media"] if m["kind"] == "image")["media_path"]
    assert any("Annual Day" in n["title"] for n in _ok(client.get("/notifications", headers=s)))

    media_id = done[0]["media"][0]["id"]
    _ok(client.delete(f"/events/{past['id']}/media/{media_id}", headers=a), 204)
    _ok(client.put(f"/events/{up['id']}", headers=a, json={"title": "Annual Day 2026",
                                                         "start_date": str(today + timedelta(days=6))}))
    _ok(client.delete(f"/events/{up['id']}", headers=a), 204)
    assert _ok(client.get(f"/events/{up['id']}", headers=s), 404)


# ------------------------------------------------------------------ transport

def test_transport_assignment_and_student_view(client, school, s1, s4):
    a = bearer(school.admin)
    driver = _ok(client.post("/users", headers=a, json={
        "username": "driver2", "password": "driverpass", "full_name": "Sonu Swami", "phone": "9351729724",
        "user_type": "driver"}), 201)["id"]
    vehicle = _ok(client.post("/vehicles", headers=a, json={
        "vehicle_no": "RJ10PA6350", "driver_name": "Sonu", "route_name": "BINASAR", "vehicle_type": "van",
        "driver_id": driver, "shift_name": "Morning", "shift_start": "07:00", "shift_end": "13:00"}), 201)["id"]
    assert _ok(client.post("/vehicles", headers=a, json={"vehicle_no": "X1", "driver_name": "d",
                                                         "driver_id": school.t1_id}), 404)
    assert _ok(client.post("/vehicles", headers=a, json={"vehicle_no": "X2", "driver_name": "d",
                                                         "shift_start": "13:00", "shift_end": "07:00"}), 400)
    _ok(client.put(f"/vehicles/{vehicle}/students/{school.s1}", headers=a, json={
        "stop_name": "Binasar rte", "pickup_time": "07:20", "drop_time": "13:40"}))
    assert _ok(client.put(f"/vehicles/{vehicle}/students/{school.t1_id}", headers=a, json={}), 404)
    assert _ok(client.put(f"/vehicles/{vehicle}/students/{school.s1}", headers=bearer(school.t1), json={}), 403)

    mine = _ok(client.get("/student/transport", headers=bearer(s1)))
    assert (mine["vehicle_no"], mine["vehicle_type"], mine["driver_name"], mine["driver_phone"]) == \
        ("RJ10PA6350", "van", "Sonu Swami", "9351729724")
    assert (mine["pickup_time"], mine["drop_time"], mine["shift_start"], mine["shift_end"]) == \
        ("07:20", "13:40", "07:00", "13:00")
    assert _ok(client.get("/student/transport", headers=bearer(s4))) is None

    rows = _ok(client.get(f"/vehicles/{vehicle}/students", headers=a))
    assert rows[0]["full_name"] == "Student 1" and rows[0]["pickup_time"] == "07:20"
    listed = _ok(client.get("/vehicles", headers=a))[0]
    assert listed["student_count"] == 1 and listed["shift_start"] == "07:00"
    _ok(client.put(f"/vehicles/{vehicle}", headers=a, json={"shift_name": "Afternoon"}))
    assert _ok(client.get("/student/transport", headers=bearer(s1)))["shift_name"] == "Afternoon"
    _ok(client.delete(f"/vehicles/{vehicle}/students/{school.s1}", headers=a), 204)
    assert _ok(client.get("/student/transport", headers=bearer(s1))) is None


# ------------------------------------------------------------------- teachers

def test_subject_teachers_of_the_section(client, school, s1):
    a = bearer(school.admin)
    _ok(client.put(f"/users/teachers/{school.t2_id}", headers=a, json={"designation": "TGT"}))
    teachers = _ok(client.get("/student/teachers", headers=bearer(s1)))
    assert [(t["full_name"], t["is_class_teacher"], t["subjects"]) for t in teachers] == \
        [("Teacher 1", True, []), ("Teacher 2", False, ["Math"])]
    assert teachers[1]["designation"] == "TGT"
    assert "phone" not in teachers[0]
    assert any(t["designation"] == "TGT" for t in _ok(client.get("/users/teachers", headers=a)))


def test_new_student_endpoints_need_a_student(client, school):
    t = bearer(school.t1)
    for path in ("/student/announcements", "/student/attendance/overview", "/student/homework/subjects",
                 "/student/syllabus", "/student/messages", "/student/transport", "/student/teachers"):
        assert client.get(path, headers=t).status_code == 403, path


def test_owner_portal_edits_the_school_profile(client, school, s1):
    platform = {"X-Platform-Secret": "test-platform-secret", "X-Platform-Actor": "owner@example.com"}
    saved = _ok(client.put("/platform/organisations/DEFAULT/profile", headers=platform, json={
        "director_name": "Rajveer Singh Dhayal", "affiliation_no": "1730786", "session_start_month": 4}))
    assert saved["director_name"] == "Rajveer Singh Dhayal" and saved["session"]["label"].count("-") == 1
    detail = _ok(client.get("/platform/organisations/DEFAULT", headers=platform))
    assert detail["profile"]["affiliation_no"] == "1730786"
    assert _ok(client.get("/school", headers=bearer(s1)))["director_name"] == "Rajveer Singh Dhayal"
    assert _ok(client.put("/platform/organisations/NOPE/profile", headers=platform, json={}), 404)
    assert _ok(client.put("/platform/organisations/DEFAULT/profile", headers=platform,
                          json={"session_start_month": 13}), 422)
    log = _ok(client.get("/platform/activity?action=school.", headers=platform))
    assert log["items"][0]["action"] == "school.profile_update"


def test_dashboard_carries_the_school_header(client, school, s1):
    dash = _ok(client.get("/student/dashboard", headers=bearer(s1)))
    start, end = school_profile.session_bounds(4)
    assert dash["school"]["code"] == "DEFAULT"
    assert dash["school"]["session"] == school_profile.session_label(start, end)
