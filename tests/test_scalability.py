"""Pagination, unique student numbers and single-round-trip dashboards."""
import pytest
from helpers import bearer, login


def _post(client, headers, path, body, expect=201):
    r = client.post(path, headers=headers, json=body)
    assert r.status_code == expect, r.text
    return r.json()


# ---------------------------------------------------------------- pagination

def test_lists_are_unchanged_without_limit(client):
    a = bearer(login(client, "admin"))
    for i in range(7):
        _post(client, a, "/notices", {"title": f"N{i}", "body": "b"})
    r = client.get("/notices", headers=a)
    assert len(r.json()) == 7
    assert "x-total-count" not in r.headers


def test_pages_cover_the_list_exactly_once(client):
    a = bearer(login(client, "admin"))
    for i in range(7):
        _post(client, a, "/notices", {"title": f"N{i}", "body": "b"})
    full = [n["id"] for n in client.get("/notices", headers=a).json()]
    seen = []
    for offset in (0, 3, 6):
        r = client.get(f"/notices?limit=3&offset={offset}", headers=a)
        assert r.headers["x-total-count"] == "7"
        seen += [n["id"] for n in r.json()]
    assert seen == full


def test_pagination_respects_filters_and_permissions(school):
    c = school.client
    r = c.get(f"/students?class_id={school.c1}&section_id={school.c1a}&limit=1", headers=bearer(school.t1))
    assert r.status_code == 200 and len(r.json()) == 1 and r.headers["x-total-count"] == "2"
    r = c.get(f"/students?class_id={school.c2}&section_id={school.c2a}&limit=1", headers=bearer(school.t1))
    assert r.status_code == 403


def test_grouped_list_can_be_paged(school):
    c, a = school.client, bearer(school.admin)
    _post(c, a, "/fees/generate", {"class_id": school.c1, "title": "T1"})
    r = c.get("/fees/pending-students?limit=2", headers=a)
    assert len(r.json()) == 2 and r.headers["x-total-count"] == "3"


def test_transactions_keep_their_default_cap(client, db):
    a = bearer(login(client, "admin"))
    db.execute_many(
        "INSERT INTO transactions (reference, amount, method, status) VALUES (%s, 10, 'cash', 'success')",
        [(f"TXN{i:010d}",) for i in range(205)],
    )
    assert len(client.get("/transactions", headers=a).json()) == 200
    r = client.get("/transactions?limit=500", headers=a)
    assert len(r.json()) == 205 and r.headers["x-total-count"] == "205"


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "limit=5&offset=-1"])
def test_invalid_page_parameters(client, query):
    r = client.get(f"/notices?{query}", headers=bearer(login(client, "admin")))
    assert r.status_code == 422


# ---------------------------------------------------- unique student numbers

def _student(c, a, n, cls, sec, **extra):
    return c.post("/students", headers=a, json={
        "username": f"kid{n}", "password": "secret1", "full_name": f"Kid {n}",
        "class_id": cls, "section_id": sec, **extra})


def test_admission_numbers_are_unique(school):
    c, a = school.client, bearer(school.admin)
    r = _student(c, a, 10, school.c2, school.c2a, admission_no="ADM1")  # ADM1 belongs to student 1
    assert r.status_code == 409 and r.json() == {"detail": "Admission number already exists"}
    assert c.get("/users?user_type=student&limit=50", headers=a).headers["x-total-count"] == "4"  # nothing half-created


def test_roll_numbers_are_unique_per_section(school):
    c, a = school.client, bearer(school.admin)
    r = _student(c, a, 11, school.c1, school.c1a, roll_no="1")  # roll 1 taken in 1-A
    assert r.status_code == 409 and r.json() == {"detail": "Roll number already used in this section"}
    assert _student(c, a, 12, school.c2, school.c2a, roll_no="1").status_code == 201  # other section is fine
    r = c.put(f"/students/{school.s2}", headers=a, json={"full_name": "Student 2", "roll_no": "1"})
    assert r.status_code == 409


def test_blank_numbers_are_stored_as_none(school):
    c, a = school.client, bearer(school.admin)
    ids = [_student(c, a, n, school.c2, school.c2a, admission_no="  ", roll_no="").json()["id"] for n in (20, 21)]
    for student_id in ids:
        s = c.get(f"/students/{student_id}", headers=a).json()
        assert s["admission_no"] is None and s["roll_no"] is None


# ------------------------------------------------------------- dashboards

def test_admin_dashboard_numbers(school, db):
    c, a = school.client, bearer(school.admin)
    c.post("/attendance", headers=bearer(school.t1), json={
        "section_id": school.c1a, "date": str(db.scalar("SELECT CURDATE()")),
        "records": [{"student_id": school.s1, "status": "present"},
                    {"student_id": school.s2, "status": "absent"}]})
    fee = _post(c, a, "/fees", {"student_id": school.s1, "title": "T", "amount": 1500})
    c.put(f"/fees/{fee['id']}/pay", headers=a, json={"method": "upi"})
    _post(c, a, "/enquiries", {"parent_name": "P", "student_name": "K", "contact": "99999"})
    stats = c.get("/dashboard/stats", headers=a).json()
    assert stats == {
        "role": "admin", "students": 4, "teachers": 3, "classes": 2, "sections": 3, "lesson_plans": 0,
        "notices": 0, "attendance_today": 50.0, "fees_collected": 1500.0, "enquiries_today": 1,
    }


def test_teacher_and_student_dashboards(school):
    c = school.client
    t = c.get("/dashboard/stats", headers=bearer(school.t1)).json()
    assert t == {"role": "teacher", "my_classes": 1, "my_students": 2, "my_lesson_plans": 0, "notices": 0}
    d = c.get("/student/dashboard", headers=bearer(login(c, "student1", "studpass"))).json()
    assert d["attendance"] == {"total": 0, "present": 0, "absent": 0, "on_leave": 0, "percent": None}
    assert d["pending_fees"] == {"count": 0, "amount_due": 0.0} and d["pending_homework"] == 0
