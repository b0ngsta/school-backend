"""The fixes from the Oct 2026 code review: people leaving keep their history,
sign-in protection that doesn't lock out a whole network, input checks,
marks per subject, and durable background jobs with a daily clean-up."""
from __future__ import annotations

from datetime import timedelta

import pymysql
import pytest
from helpers import CAPTCHA_TEXT, bearer, client_from, login, png_bytes, post_login

from app.core.clock import school_today, utcnow
from app.services import background, retention


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json() if r.content else None


# ------------------------------------------------------------ people leaving

def test_teacher_who_leaves_keeps_the_attendance_and_remarks_they_wrote(school, db):
    c, a, t1 = school.client, bearer(school.admin), bearer(school.t1)
    _ok(c.post("/attendance", headers=t1, json={
        "section_id": school.c1a, "date": str(school_today()),
        "records": [{"student_id": school.s1, "status": "present"}, {"student_id": school.s2, "status": "absent"}]}))
    _ok(c.post(f"/students/{school.s1}/remarks", headers=t1, json={"remark": "Good work"}), 201)

    res = _ok(c.delete(f"/users/{school.t1_id}?reason=Moved to another city", headers=a))
    assert res["deleted"] == school.t1_id and res["left_on"] == str(school_today())

    assert db.scalar("SELECT COUNT(*) FROM attendance WHERE student_id IN (%s, %s)", (school.s1, school.s2)) == 2
    remarks = _ok(c.get(f"/students/{school.s1}/remarks", headers=a))
    assert [r["created_by_name"] for r in remarks] == ["Teacher 1"]
    # signed out at once and can't sign in again
    assert c.get("/auth/me", headers=t1).status_code == 401
    assert c.post("/auth/login", json={"username": "teacher1", "password": "teachpass"}).status_code == 403
    # off current lists, findable as left, current duties removed
    assert school.t1_id not in {u["id"] for u in _ok(c.get("/users?user_type=teacher", headers=a))}
    left = _ok(c.get("/users?user_type=teacher&status=left", headers=a))
    assert [(u["id"], u["left_reason"]) for u in left] == [(school.t1_id, "Moved to another city")]
    assert db.scalar("SELECT COUNT(*) FROM teacher_assignments WHERE teacher_id = %s", (school.t1_id,)) == 0
    # the username stays reserved for the account, with a hint
    r = c.post("/users", headers=a, json={"username": "teacher1", "password": "teachpass", "full_name": "New",
                                         "user_type": "teacher"})
    assert r.status_code == 409 and "marked as left" in r.json()["detail"]
    # and the account can come back
    _ok(c.post(f"/users/{school.t1_id}/reactivate", headers=a))
    assert login(c, "teacher1", "teachpass")["user_id"] == school.t1_id


def test_student_who_leaves_keeps_fees_and_payments(school, db):
    c, a, t1 = school.client, bearer(school.admin), bearer(school.t1)
    fee = _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Term 1", "amount": 5000}), 201)
    _ok(c.put(f"/fees/{fee['id']}/pay", headers=a, json={"method": "cash"}))

    # only the office marks a student as left; a class teacher can't
    assert c.delete(f"/students/{school.s1}", headers=t1).status_code == 403
    _ok(c.delete(f"/students/{school.s1}", headers=a))

    assert db.scalar("SELECT COUNT(*) FROM fees WHERE id = %s", (fee["id"],)) == 1
    assert db.one("SELECT student_id FROM transactions WHERE fee_id = %s", (fee["id"],))["student_id"] == school.s1
    # gone from the class list and the attendance sheet, still findable
    section = _ok(c.get(f"/students?class_id={school.c1}&section_id={school.c1a}", headers=a))
    assert school.s1 not in {s["id"] for s in section}
    sheet = _ok(c.get(f"/attendance?section_id={school.c1a}&day={school_today()}", headers=a))
    assert school.s1 not in {r["student_id"] for r in sheet}
    left = _ok(c.get(f"/students?status=left&class_id={school.c1}", headers=a))
    assert [(s["id"], s["last_section_name"]) for s in left] == [(school.s1, "A")]
    # coming back puts them in their old section
    back = _ok(c.post(f"/students/{school.s1}/reactivate", headers=a))
    assert back["section_id"] == school.c1a
    assert school.s1 in {s["id"] for s in _ok(c.get(f"/students?section_id={school.c1a}&class_id={school.c1}",
                                                    headers=a))}


def test_database_refuses_to_delete_an_account_with_history(school, db):
    c, a = school.client, bearer(school.admin)
    _ok(c.post("/fees", headers=a, json={"student_id": school.s4, "title": "Term 1", "amount": 100}), 201)
    with pytest.raises(pymysql.err.IntegrityError):
        db.execute("DELETE FROM users WHERE id = %s", (school.s4,))
    # authors only lose their name on a record (SET NULL); the record stays
    _ok(c.post("/notices", headers=a, json={"title": "Hello", "body": "x"}), 201)
    columns = {r["COLUMN_NAME"]: r["IS_NULLABLE"] for r in db.all(
        "SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.columns WHERE table_schema = DATABASE() "
        "AND table_name = 'notices'")}
    assert columns["created_by"] == "YES"


def test_a_new_principal_can_be_appointed_when_the_old_one_leaves(client):
    a = bearer(login(client, "admin"))
    principal = next(u for u in _ok(client.get("/users?user_type=principal", headers=a)))
    _ok(client.delete(f"/users/{principal['id']}", headers=a))
    _ok(client.post("/users", headers=a, json={"username": "newhead", "password": "headpass1",
                                               "full_name": "New Head", "user_type": "principal"}), 201)
    # the old one can't come back while there is a principal
    assert client.post(f"/users/{principal['id']}/reactivate", headers=a).status_code == 409


# ------------------------------------------------------------ sign-in protection

def test_shared_address_no_longer_locks_out_people_with_the_right_password(school):
    shared = client_from("49.36.10.10")  # a mobile carrier NAT or the school Wi-Fi
    for n in range(60):
        r = post_login(shared, {"username": f"someone{n}", "password": "wrong-password"},
                       headers={"X-Device-Id": f"device-{n:04d}-x"})
        assert r.status_code == 401, r.text
    ok = post_login(shared, {"username": "student1", "password": "studpass"},
                    headers={"X-Device-Id": "student1-phone"})
    assert ok.status_code == 200, ok.text


def test_apps_without_captcha_keep_the_original_limits(school):
    shared = client_from("49.36.10.11")
    for n in range(50):
        shared.post("/auth/login", json={"username": f"someone{n}", "password": "wrong-password"})
    assert shared.post("/auth/login", json={"username": "student1", "password": "studpass"}).status_code == 429


def test_failures_on_one_account_ask_for_a_captcha_then_lock(school):
    c = client_from("10.1.1.1")
    body = {"username": "student1", "password": "nope", "captcha_supported": True}
    for _ in range(3):
        assert c.post("/auth/login", json=body).status_code == 401
    r = c.post("/auth/login", json=body)
    assert r.status_code == 428 and r.json()["code"] == "captcha_required"
    pic = _ok(c.post("/auth/captcha"))
    assert pic["image"].startswith("data:image/png;base64,") and pic["length"] == 5
    wrong = c.post("/auth/login", json={**body, "password": "studpass", "captcha_id": pic["captcha_id"],
                                        "captcha_answer": "ZZZZZ"})
    assert wrong.status_code == 428 and wrong.json()["code"] == "captcha_invalid"
    # a picture works once (here: solved, but the password was wrong)
    pic = _ok(c.post("/auth/captcha"))
    solved = {**body, "captcha_id": pic["captcha_id"], "captcha_answer": CAPTCHA_TEXT}
    assert c.post("/auth/login", json=solved).status_code == 401
    assert c.post("/auth/login", json={**solved, "password": "studpass"}).json()["code"] == "captcha_invalid"
    # answers aren't case-sensitive
    pic = _ok(c.post("/auth/captcha"))
    good = {**body, "password": "studpass", "captcha_id": pic["captcha_id"], "captcha_answer": CAPTCHA_TEXT.lower()}
    assert c.post("/auth/login", json=good).status_code == 200
    # after 10 failures the account is locked even with a solved picture
    for _ in range(10):
        pic = _ok(c.post("/auth/captcha"))
        c.post("/auth/login", json={**body, "captcha_id": pic["captcha_id"], "captcha_answer": CAPTCHA_TEXT})
    pic = _ok(c.post("/auth/captcha"))
    assert c.post("/auth/login", json={**good, "captcha_id": pic["captcha_id"]}).status_code == 429


def test_one_device_guessing_one_account_is_stopped(school):
    c = client_from("10.1.1.2")
    headers = {"X-Device-Id": "guessing-phone-1"}
    for _ in range(5):
        assert post_login(c, {"username": "student2", "password": "nope"}, headers=headers).status_code == 401
    assert post_login(c, {"username": "student2", "password": "studpass"}, headers=headers).status_code == 429
    # the same account from the owner's own phone still works
    assert post_login(c, {"username": "student2", "password": "studpass"},
                      headers={"X-Device-Id": "owners-phone-1"}).status_code == 200


# ------------------------------------------------------------ input checks

def test_attendance_cannot_be_marked_for_a_future_date(school):
    c, t1 = school.client, bearer(school.t1)
    r = c.post("/attendance", headers=t1, json={
        "section_id": school.c1a, "date": str(school_today() + timedelta(days=1)),
        "records": [{"student_id": school.s1, "status": "present"}]})
    assert r.status_code == 400 and "future" in r.json()["detail"]


def test_exam_marks_per_subject_with_a_maximum(school):
    c, a, t1 = school.client, bearer(school.admin), bearer(school.t1)
    _ok(c.put(f"/classes/{school.c1}/subjects", headers=a, json={"subjects": ["Hindi", "Math"]}))
    exam = _ok(c.post("/exams", headers=a, json={"name": "Mid term", "class_id": school.c1, "max_marks": 50,
                                                 "start_date": "2026-09-01", "end_date": "2026-09-10"}), 201)
    path = f"/exams/{exam['id']}/results"

    def save(results, **extra):
        return c.put(path, headers=t1, json={"section_id": school.c1a, "results": results, **extra})

    assert save([{"student_id": school.s1, "marks": -4}], subject="Math").status_code == 422
    too_high = save([{"student_id": school.s1, "marks": 51}], subject="Math")
    assert too_high.status_code == 400 and "maximum (50)" in too_high.json()["detail"]
    assert save([{"student_id": school.s1, "marks": 10}], subject="Science").status_code == 400
    _ok(save([{"student_id": school.s1, "marks": 45}, {"student_id": school.s2, "marks": 30}], subject="Math"))
    _ok(save([{"student_id": school.s1, "marks": 80}], subject="Hindi", max_marks=100))
    # the whole-exam mark (older apps) is separate and still works
    _ok(save([{"student_id": school.s2, "marks": 40, "grade": "B"}]))

    math = _ok(c.get(f"{path}?section_id={school.c1a}&subject=Math", headers=t1))
    assert (math["subject"], math["max_marks"], math["subjects"]) == ("Math", 50, ["Hindi", "Math"])
    assert {r["student_id"]: r["marks"] for r in math["rows"]} == {school.s1: 45, school.s2: 30}
    whole = _ok(c.get(f"{path}?section_id={school.c1a}", headers=t1))
    assert {r["student_id"]: r["marks"] for r in whole["rows"]}[school.s2] == 40

    _ok(c.put(f"/exams/{exam['id']}", headers=a, json={"results_published": True}))
    mine = next(e for e in _ok(c.get("/student/exams", headers=bearer(login(c, "student1", "studpass"))))
                if e["id"] == exam["id"])
    subjects = [(s["subject"], s["marks"], s["max_marks"]) for s in mine["subjects"]]
    assert subjects == [("Math", 45, 50), ("Hindi", 80, 100)]
    assert (mine["total"], mine["max_total"]) == (125, 150)
    assert (mine["marks"], mine["max_marks"]) == (125, 150)  # older app builds show `marks`


# ------------------------------------------------------------ background jobs

@background.task(max_attempts=2)
def _flaky_job(marker: str, when) -> None:
    _calls.append((marker, when))
    if marker == "fail":
        raise RuntimeError("provider down")


_calls: list = []


@pytest.fixture
def stored_jobs(monkeypatch):
    """Jobs are stored instead of run inline, as in production."""
    monkeypatch.setattr(background, "run_inline", False)
    _calls.clear()
    yield
    _calls.clear()


def test_jobs_are_stored_encrypted_and_survive_until_a_worker_runs_them(stored_jobs, db):
    now = utcnow()
    with db.transaction():
        background.enqueue(db, _flaky_job, "ok secret-text", now)
    row = db.one("SELECT kind, payload, status FROM background_jobs")
    assert row["kind"].endswith(":_flaky_job") and row["status"] == "pending"
    assert "secret-text" not in row["payload"]  # encrypted at rest
    assert background.run_pending() == 1
    assert _calls == [("ok secret-text", now)]  # datetimes come back as datetimes
    done = db.one("SELECT status, payload FROM background_jobs")
    assert done == {"status": "done", "payload": None}


def test_a_rolled_back_change_stores_no_job(stored_jobs, db):
    with pytest.raises(ZeroDivisionError), db.transaction():
        background.enqueue(db, _flaky_job, "never", utcnow())
        1 / 0  # noqa: B018
    assert db.scalar("SELECT COUNT(*) FROM background_jobs") == 0


def test_failed_jobs_are_retried_then_kept_as_failed(stored_jobs, db):
    background.enqueue(db, _flaky_job, "fail", utcnow())
    assert background.run_pending() == 1
    row = db.one("SELECT status, attempts, run_after, last_error FROM background_jobs")
    assert (row["status"], row["attempts"]) == ("pending", 1) and "provider down" in row["last_error"]
    assert row["run_after"] > utcnow()
    db.execute("UPDATE background_jobs SET run_after = %s", (utcnow(),))
    assert background.run_pending() == 1
    assert db.one("SELECT status, attempts FROM background_jobs") == {"status": "failed", "attempts": 2}
    assert background.retry_failed() == 1


def test_a_job_left_running_by_a_dead_process_runs_again(stored_jobs, db):
    background.enqueue(db, _flaky_job, "ok", utcnow())
    db.execute("UPDATE background_jobs SET status = 'running', locked_at = %s", (utcnow() - timedelta(hours=1),))
    assert background.run_pending() == 1 and len(_calls) == 1


def test_push_messages_become_jobs_in_the_same_transaction(stored_jobs, school, monkeypatch, db):
    from app.services import notify

    monkeypatch.setattr(notify, "push_configured", lambda: True)
    monkeypatch.setattr(notify, "_firebase_app", lambda: None)  # no Firebase here: the job just completes
    c, a = school.client, bearer(school.admin)
    _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Bus", "amount": 300}), 201)
    jobs = db.all("SELECT kind, status FROM background_jobs")
    assert [(j["kind"], j["status"]) for j in jobs] == [("app.services.notify:_send_push", "pending")]
    assert background.run_pending() == 1
    assert db.scalar("SELECT status FROM background_jobs") == "done"


# ------------------------------------------------------------ daily clean-up

def test_cleanup_removes_only_old_rows(school, db):
    uid = school.s1
    db.execute("INSERT INTO notifications (user_id, type, title, created_at) VALUES "
               "(%s, 'x', 'old', NOW() - INTERVAL 400 DAY), (%s, 'x', 'new', NOW())", (uid, uid))
    db.execute("INSERT INTO audit_logs (action, created_at) VALUES "
               "('old.thing', NOW() - INTERVAL 800 DAY), ('new.thing', NOW())")
    removed = retention.run(db)
    assert removed["notifications"] == 1 and removed["audit_logs"] == 1
    assert [r["title"] for r in db.all("SELECT title FROM notifications WHERE user_id = %s", (uid,))] == ["new"]
    assert db.scalar("SELECT COUNT(*) FROM audit_logs WHERE action = 'old.thing'") == 0


def test_cleanup_runs_once_a_day_across_processes(db):
    assert retention.run_if_due() is not None
    assert retention.run_if_due() is None  # another process (or this one) already ran it today


def test_claim_review_still_works_with_left_students(school):
    """A payment proof sent before the student left can still be reviewed."""
    c, a = school.client, bearer(school.admin)
    fee = _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "T1", "amount": 900}), 201)
    claim = _ok(c.post(f"/fees/{fee['id']}/claims", headers=bearer(login(c, "student1", "studpass")),
                       files={"file": ("p.png", png_bytes(), "image/png")}), 201)
    _ok(c.delete(f"/students/{school.s1}", headers=a))
    _ok(c.put(f"/fees/claims/{claim['id']}/approve", headers=a, json={}))
