"""Organisations: several schools on one API, and nothing crosses between them.

School A is the default school (organisation 1, code DEFAULT); school B is
created through the platform endpoint. Both are filled with the same kind of
data under the same usernames and class names, each labelled with its own
marker word, then every list endpoint is read as each role of each school.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from helpers import PDF_BYTES, bearer, client_from, login
from test_password_reset import code_from, outbox  # noqa: F401 - fixture

from app.core.clock import school_today
from app.routes import ALL_ROUTERS

PLATFORM = {"X-Platform-Secret": "test-platform-secret"}
ALPHA, BRAVO = "Alphazulu", "Bravoyankee"


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def next_weekday(weekday: int) -> date:
    start = school_today() + timedelta(days=1)
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def create_school(client, code="ABC", name="ABC Public School", password="abc-admin-pass"):
    created = _ok(client.post("/platform/organisations", headers=PLATFORM, json={
        "code": code, "name": name, "admin_password": password, "admin_email": "office@abc.example"}), 201)
    return created


def login_to(client, org_code, username, password):
    body = {"username": username, "password": password}
    if org_code is not None:
        body["org_code"] = org_code
    return client.post("/auth/login", json=body)


class Filled:
    """One school filled through the API, every name carrying `mark`."""

    def __init__(self, client, org_code, admin, mark):
        self.client, self.org_code, self.mark = client, org_code, mark
        c, a = client, bearer(admin)
        self.admin = admin

        def post(path, body, code=201):
            return _ok(c.post(path, json=body, headers=a), code)

        self.teacher = post("/users", {"username": "teacher1", "password": "teachpass",
                                       "full_name": f"{mark} Teacher", "user_type": "teacher"})["id"]
        self.teacher2 = post("/users", {"username": "teacher2", "password": "teachpass",
                                        "full_name": f"{mark} Teacher Two", "user_type": "teacher"})["id"]
        self.principal = post("/users", {"username": "principal2", "password": "principalpass",
                                         "full_name": f"{mark} Principal", "user_type": "principal"},
                              201 if org_code != "DEFAULT" else 409)
        self.cls = post("/classes", {"name": "Class 1", "fee_amount": 5000, "subjects": [f"{mark} Maths"]})["id"]
        self.sec = post(f"/classes/{self.cls}/sections", {"name": "A"})["id"]
        post("/assignments", {"teacher_id": self.teacher, "class_id": self.cls, "section_id": self.sec,
                              "role": "class_teacher"})
        self.student = post("/students", {
            "username": "student1", "password": "studpass", "full_name": f"{mark} Student", "roll_no": "1",
            "admission_no": "ADM1", "class_id": self.cls, "section_id": self.sec})["id"]
        self.notice = post("/notices", {"title": f"{mark} notice", "body": f"{mark} body"})["id"]
        self.enquiry = post("/enquiries", {"parent_name": f"{mark} Parent", "student_name": f"{mark} Kid",
                                           "contact": "9999999999"})["id"]
        self.vehicle = post("/vehicles", {"vehicle_no": "KA01AB1234", "driver_name": f"{mark} Driver",
                                          "route_name": f"{mark} Route"})["id"]
        self.holiday = post("/holidays", {"date": str(school_today() + timedelta(days=40)),
                                          "name": f"{mark} Day"})["id"]
        self.event = post("/calendar/events", {"title": f"{mark} Fair", "category": "holiday"})["id"]
        _ok(c.put(f"/calendar/events/{self.event}", headers=a, json={
            "title": f"{mark} Fair", "category": "holiday", "dates": [str(school_today() + timedelta(days=41))]}))
        self.exam = post("/exams", {"name": f"{mark} Exam", "start_date": str(school_today()),
                                    "end_date": str(school_today() + timedelta(days=60))})["id"]
        self.paper = _ok(c.post(f"/exams/{self.exam}/classes/{self.cls}/papers", headers=a,
                                data={"subject": f"{mark} Maths"},
                                files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")}), 201)["id"]
        self.fee = post("/fees", {"student_id": self.student, "title": f"{mark} Term fee", "amount": 1200,
                                  "due_date": str(school_today() + timedelta(days=2))})["id"]
        post("/fees/generate", {"class_id": self.cls, "title": f"{mark} Annual"})
        self.paid_fee = post("/fees", {"student_id": self.student, "title": f"{mark} Bus fee", "amount": 300})["id"]
        _ok(c.put(f"/fees/{self.paid_fee}/pay", headers=a, json={"method": "cash"}))
        post("/transactions", {"student_id": self.student, "amount": 50, "method": "upi"})
        post("/sms", {"recipient_group": "all_parents", "message": f"{mark} sms", "template": f"{mark} tpl"})
        post("/salaries", {"user_id": self.teacher, "month": 1, "year": 2026, "amount": 30000,
                           "note": f"{mark} salary"})
        _ok(c.put("/settings/school-timings", headers=a, json={
            "start_time": "07:00" if mark == ALPHA else "09:30", "period_minutes": 40, "periods_per_day": 6}))
        self.tuesday = next_weekday(1)
        self.slot = _ok(c.put("/timetable", headers=a, json={
            "teacher_id": self.teacher, "day": 1, "period": 2, "class_id": self.cls, "section_id": self.sec,
            "subject": f"{mark} Maths"}))["id"]
        self.remark = post(f"/students/{self.student}/remarks", {"remark": f"{mark} remark"})["id"]
        self.report = post(f"/students/{self.student}/report-cards", {"term": f"{mark} term"})["id"]

        self.t = login(c, "teacher1", "teachpass") if org_code == "DEFAULT" else \
            _ok(login_to(c, org_code, "teacher1", "teachpass"))
        self.s = login(c, "student1", "studpass") if org_code == "DEFAULT" else \
            _ok(login_to(c, org_code, "student1", "studpass"))
        t = bearer(self.t)
        self.homework = _ok(c.post("/homework", headers=t, json={
            "class_id": self.cls, "section_id": self.sec, "title": f"{mark} homework"}), 201)["id"]
        _ok(c.post("/attendance", headers=t, json={
            "section_id": self.sec, "date": str(school_today()),
            "records": [{"student_id": self.student, "status": "present"}]}))
        _ok(c.post("/staff-attendance/check-in", headers=t))
        self.lesson = _ok(c.post("/lessons", headers=t, data={"class_id": self.cls, "section_id": self.sec,
                                                               "heading": f"{mark} lesson"},
                                 files={"files": ("plan.pdf", PDF_BYTES, "application/pdf")}), 201)
        self.leave = _ok(c.post("/leaves", headers=t, json={
            "from_date": str(self.tuesday), "to_date": str(self.tuesday), "reason": f"{mark} leave"}), 201)["id"]
        self.substitution = _ok(c.post("/substitutions", headers=t, json={
            "date": str(self.tuesday), "period": 2, "substitute_id": self.teacher2, "note": f"{mark} cover"}),
            201)["id"]
        self.claim = _ok(c.post(f"/fees/{self.fee}/claims", headers=bearer(self.s),
                                data={"method": "upi", "note": f"{mark} proof"},
                                files={"file": ("proof.png", _png(), "image/png")}), 201)["id"]


def _png() -> bytes:
    from helpers import png_bytes
    return png_bytes()


@pytest.fixture
def schools(client):
    a_admin = login(client, "admin")
    create_school(client)
    b_admin = _ok(login_to(client, "ABC", "admin", "abc-admin-pass"))
    return Filled(client, "DEFAULT", a_admin, ALPHA), Filled(client, "ABC", b_admin, BRAVO)


# ------------------------------------------------------------ sign-in

def test_same_username_in_two_schools_are_different_accounts(client, schools):
    a, b = schools
    in_a = _ok(login_to(client, "DEFAULT", "teacher1", "teachpass"))
    in_b = _ok(login_to(client, "abc", "teacher1", "teachpass"))  # codes are case-insensitive
    assert in_a["user_id"] == a.teacher and in_b["user_id"] == b.teacher != a.teacher
    assert (in_a["org_code"], in_b["org_code"]) == ("DEFAULT", "ABC")
    assert in_b["org_name"] == "ABC Public School"
    me = _ok(client.get("/auth/me", headers=bearer(in_b)))
    assert me["organisation"] == {"id": in_b["org_id"], "code": "ABC", "name": "ABC Public School"}
    # a password from one school never opens the other school's account
    assert login_to(client, "ABC", "admin", "admin123").status_code == 401


def test_login_without_a_code_uses_the_default_school(client, schools):
    a, _ = schools
    body = _ok(login_to(client, None, "teacher1", "teachpass"))
    assert body["user_id"] == a.teacher and body["org_code"] == "DEFAULT"


def test_unknown_and_malformed_school_codes(client):
    r = login_to(client, "NOPE", "admin", "admin123")
    assert r.status_code == 400 and "School code not found" in r.json()["detail"]
    assert client.get("/organisations/NOPE").status_code == 404
    assert client.get("/organisations/a b").status_code == 404
    assert _ok(client.get("/organisations/default"))["code"] == "DEFAULT"
    r = client.post("/auth/forgot-password", json={"org_code": "NOPE", "login": "admin"})
    assert r.status_code == 400


def test_failed_logins_in_one_school_do_not_lock_the_other(client):
    create_school(client)
    for i in range(10):  # the per-account limit, from many addresses
        assert login_to(client_from(f"10.9.9.{i}"), "DEFAULT", "admin", "wrong").status_code == 401
    assert login_to(client_from("10.9.8.1"), "DEFAULT", "admin", "admin123").status_code == 429
    # school B's "admin" is another account with its own counters
    _ok(login_to(client_from("10.9.8.2"), "ABC", "admin", "abc-admin-pass"))


def test_disabling_a_school_signs_its_users_out(client, schools):
    _, b = schools
    a_token = login(client, "admin")
    b_token = _ok(login_to(client, "ABC", "teacher1", "teachpass"))
    disabled = _ok(client.patch("/platform/organisations/abc", headers=PLATFORM, json={"is_active": False}))
    assert disabled["is_active"] == 0
    assert client.get("/auth/me", headers=bearer(b_token)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": b_token["refresh_token"]}).status_code == 401
    assert login_to(client, "ABC", "teacher1", "teachpass").status_code == 403
    assert client.get("/organisations/ABC").status_code == 404
    _ok(client.get("/auth/me", headers=bearer(a_token)))  # other schools carry on
    _ok(client.patch("/platform/organisations/ABC", headers=PLATFORM, json={"is_active": True, "name": "ABC School"}))
    _ok(client.post("/auth/refresh", json={"refresh_token": b_token["refresh_token"]}))
    assert _ok(client.get("/organisations/abc"))["name"] == "ABC School"
    # a new login code: the old one stops working, sessions carry on
    assert client.patch("/platform/organisations/ABC", headers=PLATFORM, json={"code": "default"}).status_code == 409
    _ok(client.patch("/platform/organisations/ABC", headers=PLATFORM, json={"code": "xyz-ps"}))
    assert login_to(client, "ABC", "teacher1", "teachpass").status_code == 400
    assert _ok(login_to(client, "XYZ-PS", "teacher1", "teachpass"))["org_code"] == "XYZ-PS"
    _ok(client.get("/auth/me", headers=bearer(b_token)))


def test_platform_endpoints_need_the_secret(client):
    assert client.get("/platform/organisations").status_code == 403
    assert client.get("/platform/organisations", headers={"X-Platform-Secret": "nope"}).status_code == 403
    create_school(client)
    r = client.post("/platform/organisations", headers=PLATFORM,
                    json={"code": "abc", "name": "Again", "admin_password": "whatever1"})
    assert r.status_code == 409
    assert client.post("/platform/organisations", headers=PLATFORM,
                       json={"code": "a b!", "name": "Bad", "admin_password": "whatever1"}).status_code == 400
    listed = {o["code"]: o for o in _ok(client.get("/platform/organisations", headers=PLATFORM))}
    assert set(listed) == {"DEFAULT", "ABC"} and listed["ABC"]["staff"] == 1
    # the new school's admin must choose a password first, and starts with the usual calendar events
    admin = _ok(login_to(client, "ABC", "admin", "abc-admin-pass"))
    assert admin["must_change_password"] is True
    titles = {e["title"] for e in _ok(client.get("/calendar", headers=bearer(admin)))["events"]}
    assert titles == {"Holiday", "Exam", "PTM", "Annual Function Day"}


def test_password_reset_is_per_school(client, schools, outbox):  # noqa: F811
    a, b = schools
    for school, uid in ((a, a.teacher), (b, b.teacher)):
        _ok(client.put(f"/users/{uid}", headers=bearer(school.admin), json={"email": "same@example.com"}))
    _ok(client.post("/auth/forgot-password", json={"org_code": "ABC", "login": "teacher1"}))
    [mail] = outbox.to("same@example.com")
    assert "ABC Public School" in mail.subject
    code = code_from(mail)
    # the code belongs to school B's teacher1 only
    assert client.post("/auth/reset-password", json={"login": "teacher1", "code": code,
                                                     "new_password": "brand-new-1"}).status_code == 400
    reset = _ok(client.post("/auth/reset-password", json={"org_code": "abc", "login": "teacher1", "code": code,
                                                          "new_password": "brand-new-1"}))
    assert reset["user_id"] == b.teacher and reset["org_code"] == "ABC"
    _ok(login_to(client, "DEFAULT", "teacher1", "teachpass"))  # school A's teacher1 is untouched
    # an email address shared across schools only lists the accounts of the school asked about
    _ok(client.post("/auth/forgot-password", json={"login": "same@example.com"}))
    assert "ABC" not in outbox.to("same@example.com")[-1].text


# ------------------------------------------------------- data isolation

def _get_routes() -> list[str]:
    paths = set()
    for router in ALL_ROUTERS:
        for route in router.routes:
            if "GET" in route.methods and "{" not in route.path and not route.path.startswith(
                    ("/auth", "/platform", "/internal", "/uploads")):
                paths.add(route.path)
    return sorted(paths)


def _sweep(client, who: dict, extra: list[str]) -> dict[str, str]:
    out = {}
    for path in _get_routes() + extra:
        r = client.get(path, headers=bearer(who))
        if r.status_code == 200:
            out[path] = r.text
    return out


def test_every_list_shows_only_the_own_school(client, schools):
    a, b = schools
    for me, other in ((a, b), (b, a)):
        extra = [
            f"/attendance?section_id={me.sec}&day={school_today()}",
            f"/students?class_id={me.cls}&section_id={me.sec}",
            f"/homework?class_id={me.cls}&section_id={me.sec}",
            f"/exams/{me.exam}", f"/exams/{me.exam}/results?section_id={me.sec}",
            f"/exams/{me.exam}/classes/{me.cls}/papers",
            f"/students/{me.student}", f"/students/{me.student}/remarks",
            f"/students/{me.student}/report-cards", f"/students/{me.student}/homework",
            f"/students/{me.student}/attendance", f"/leaves/{me.leave}",
            f"/substitutions/{me.substitution}", f"/homework/{me.homework}/submissions",
            f"/timetable/week?start={me.tuesday}&teacher_id={me.teacher}",
            f"/substitutions/availability?from={me.tuesday}&to={me.tuesday}",
            f"/imports/users/export?user_type=student&class_id={me.cls}&section_id={me.sec}",
            "/imports/users/export?user_type=teacher",
            "/staff-attendance?limit=50", "/fees/claims?limit=50", "/transactions?limit=50",
        ]
        seen_own = 0
        for role in (me.admin, me.t, me.s):
            pages = _sweep(client, role, extra)
            assert len(pages) > 15
            if role is me.admin:  # every extra query is valid, so none was skipped silently
                assert not [p for p in extra if p not in pages], [p for p in extra if p not in pages]
            for path, text in pages.items():
                assert other.mark not in text, f"{path} shows the other school's data"
            seen_own += sum(me.mark in text for text in pages.values())
        assert seen_own > 40  # the sweep really saw this school's own data
        stats = _ok(client.get("/dashboard/stats", headers=bearer(me.admin)))
        assert (stats["students"], stats["teachers"], stats["classes"], stats["sections"]) == (1, 2, 1, 1)
        timings = _ok(client.get("/settings/school-timings", headers=bearer(me.admin)))
        assert timings["start_time"] == ("07:00" if me.mark == ALPHA else "09:30")


def test_ids_from_another_school_are_not_found(client, schools):
    a, b = schools
    c, h = client, bearer(b.admin)
    t = bearer(b.t)
    blocked = [
        c.get(f"/students/{a.student}", headers=h),
        c.put(f"/students/{a.student}", headers=h, json={"full_name": "Hijacked"}),
        c.delete(f"/students/{a.student}", headers=h),
        c.post(f"/students/{a.student}/remarks", headers=h, json={"remark": "x"}),
        c.put(f"/users/{a.teacher}", headers=h, json={"full_name": "Hijacked"}),
        c.delete(f"/users/{a.teacher}", headers=h),
        c.put(f"/users/teachers/{a.teacher}", headers=h, json={"subject": "x"}),
        c.post(f"/users/{a.teacher}/photo", headers=h, files={"file": ("p.png", _png(), "image/png")}),
        c.delete(f"/classes/{a.cls}", headers=h),
        c.post(f"/classes/{a.cls}/sections", headers=h, json={"name": "Z"}),
        c.put(f"/classes/{a.cls}/subjects", headers=h, json={"subjects": ["x"]}),
        c.delete(f"/classes/{a.cls}/sections/{a.sec}", headers=h),
        c.post("/assignments", headers=h, json={"teacher_id": a.teacher, "class_id": b.cls, "section_id": b.sec,
                                                "role": "subject_teacher", "subject": "x"}),
        c.post("/assignments", headers=h, json={"teacher_id": b.teacher2, "class_id": a.cls, "section_id": a.sec,
                                                "role": "subject_teacher", "subject": "x"}),
        c.post("/students", headers=h, json={"username": "intruder", "password": "intruder1", "full_name": "x",
                                             "class_id": a.cls, "section_id": a.sec}),
        c.post("/fees", headers=h, json={"student_id": a.student, "title": "x", "amount": 1}),
        c.post("/fees/generate", headers=h, json={"class_id": a.cls, "title": "x"}),
        c.put(f"/fees/{a.fee}/pay", headers=h, json={"method": "cash"}),
        c.delete(f"/fees/{a.fee}", headers=h),
        c.put(f"/fees/claims/{a.claim}/approve", headers=h, json={}),
        c.post(f"/fees/{a.fee}/claims", headers=bearer(b.s), data={"method": "upi"},
               files={"file": ("proof.png", _png(), "image/png")}),
        c.post("/transactions", headers=h, json={"student_id": a.student, "amount": 1, "method": "cash"}),
        c.delete(f"/notices/{a.notice}", headers=h),
        c.put(f"/enquiries/{a.enquiry}", headers=h, json={"status": "closed"}),
        c.delete(f"/enquiries/{a.enquiry}", headers=h),
        c.put(f"/vehicles/{a.vehicle}", headers=h, json={"driver_name": "x"}),
        c.delete(f"/vehicles/{a.vehicle}", headers=h),
        c.put(f"/holidays/{a.holiday}", headers=h, json={"date": str(school_today()), "name": "x"}),
        c.delete(f"/holidays/{a.holiday}", headers=h),
        c.put(f"/calendar/events/{a.event}", headers=h, json={"title": "x", "dates": []}),
        c.delete(f"/calendar/events/{a.event}", headers=h),
        c.get(f"/exams/{a.exam}", headers=h),
        c.put(f"/exams/{a.exam}", headers=h, json={"name": "x"}),
        c.delete(f"/exams/{a.exam}", headers=h),
        c.get(f"/exams/{a.exam}/papers/{a.paper}/file", headers=h),
        c.delete(f"/exams/{a.exam}/papers/{a.paper}", headers=h),
        c.post("/exams", headers=h, json={"name": "x", "class_id": a.cls, "start_date": str(school_today()),
                                          "end_date": str(school_today())}),
        c.get(f"/exams/{b.exam}/results?section_id={a.sec}", headers=h),
        c.get(f"/attendance?section_id={a.sec}&day={school_today()}", headers=h),
        c.post("/attendance", headers=h, json={"section_id": a.sec, "date": str(school_today()),
                                               "records": [{"student_id": a.student, "status": "absent"}]}),
        c.post("/attendance", headers=h, json={"section_id": b.sec, "date": str(school_today()),
                                               "records": [{"student_id": a.student, "status": "absent"}]}),
        c.get(f"/leaves/{a.leave}", headers=h),
        c.put(f"/leaves/{a.leave}/approve", headers=h, json={}),
        c.post("/salaries", headers=h, json={"user_id": a.teacher, "month": 2, "year": 2026, "amount": 1}),
        c.put("/timetable", headers=h, json={"teacher_id": a.teacher, "day": 2, "period": 1, "class_id": b.cls,
                                             "section_id": b.sec}),
        c.put("/timetable", headers=h, json={"teacher_id": b.teacher, "day": 2, "period": 1, "class_id": a.cls,
                                             "section_id": a.sec}),
        c.delete(f"/timetable/{a.slot}", headers=h),
        c.get(f"/substitutions/{a.substitution}", headers=h),
        c.put(f"/substitutions/{a.substitution}/cancel", headers=h, json={}),
        c.post("/substitutions", headers=t, json={"date": str(b.tuesday), "period": 2,
                                                  "substitute_id": a.teacher2}),
        c.get(f"/homework/{a.homework}/submissions", headers=h),
        c.put(f"/homework/{a.homework}", headers=h, json={"title": "x"}),
        c.delete(f"/homework/{a.homework}", headers=h),
        c.post("/homework", headers=h, json={"class_id": a.cls, "section_id": a.sec, "title": "x"}),
        c.get(f"/lessons/files/{a.lesson['files'][0]['id']}", headers=h),
        c.delete(f"/lessons/{a.lesson['id']}", headers=h),
        c.post("/sms", headers=h, json={"recipient_group": f"section:{a.sec}", "message": "x"}),
        c.get(f"/imports/users/export?user_type=student&class_id={a.cls}&section_id={a.sec}", headers=h),
    ]
    for r in blocked:
        assert r.status_code in (400, 403, 404), f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    # school A is exactly as it was
    a_admin = bearer(a.admin)
    assert _ok(c.get(f"/students/{a.student}", headers=a_admin))["full_name"] == f"{ALPHA} Student"
    assert len(_ok(c.get("/notices", headers=a_admin))) == 1
    assert _ok(c.get(f"/exams/{a.exam}", headers=a_admin))["name"] == f"{ALPHA} Exam"
    assert [r["status"] for r in _ok(c.get(f"/attendance?section_id={a.sec}&day={school_today()}",
                                           headers=a_admin))] == ["present"]
    assert len(_ok(c.get("/timetable", headers=a_admin))) == 1


def test_per_school_unique_rules(client, schools):
    a, b = schools
    # same class name, username, admission number, vehicle number and holiday date worked above;
    # inside one school the rules still hold
    h = bearer(b.admin)
    assert client.post("/classes", headers=h, json={"name": "Class 1"}).status_code == 409
    assert client.post("/users", headers=h, json={"username": "teacher1", "password": "teachpass",
                                                  "full_name": "x", "user_type": "teacher"}).status_code == 409
    assert client.post("/vehicles", headers=h, json={"vehicle_no": "KA01AB1234",
                                                     "driver_name": "x"}).status_code == 409
    assert client.post("/users", headers=h, json={"username": "principal3", "password": "principalpass",
                                                  "full_name": "x", "user_type": "principal"}).status_code == 409
    r = client.post("/students", headers=h, json={"username": "student9", "password": "studpass", "full_name": "x",
                                                  "admission_no": "ADM1", "class_id": b.cls, "section_id": b.sec})
    assert r.status_code == 409 and "Admission number" in r.json()["detail"]


def test_csv_import_matches_usernames_inside_the_school(client, schools):
    from test_user_import import make_csv, upload

    _, b = schools
    data = make_csv([["student1", "", f"{BRAVO} Student", "s1@b.example", "", "ADM1", "1", "", "", "", ""],
                     ["student2", "pass1234", "New Kid", "s2@b.example", "", "ADM2", "2", "", "", "", ""]])
    report = upload(client, b.admin, "student", data, section=(b.cls, b.sec), save=True).json()
    actions = {r["username"]: r["action"] for r in report["rows"]}
    assert actions == {"student1": "update", "student2": "create"}, report
    _ok(login_to(client, "ABC", "student2", "pass1234"))
    assert login_to(client, "DEFAULT", "student2", "pass1234").status_code == 401
