"""Regression tests for the bugs fixed in v3 (races, partial updates,
timezone, file cleanup, connection loss)."""
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pymysql
import pytest
from helpers import bearer, jpeg_with_gps, login, png_bytes

from app.core.config import settings
from app.core.errors import Conflict
from app.db import Db, pool
from app.models.auth import CurrentUser
from app.services import fees as fee_service
from app.services import storage

IST = ZoneInfo("Asia/Kolkata")


def _actor(db, username="admin", org_id=1) -> CurrentUser:
    row = db.one("SELECT u.*, o.code, o.name AS org_name FROM users u JOIN organisations o ON o.id = u.org_id "
                 "WHERE u.org_id = %s AND u.username = %s", (org_id, username))
    return CurrentUser(id=row["id"], username=row["username"], full_name=row["full_name"], email=None,
                       phone=None, user_type=row["user_type"], is_active=1, session_id="x",
                       must_change_password=False, org_id=row["org_id"], org_code=row["code"],
                       org_name=row["org_name"])


def _run_concurrently(fn, n=5, org_id=1):
    """Run fn(db, worker_index) in n threads that start at the same instant
    (each with its own session, bound to the school like an API request)."""
    results, barrier = [], threading.Barrier(n)

    def worker(index):
        db = Db(pool)
        db.bind_org(org_id)
        try:
            db.scalar("SELECT 1")  # hold a live connection so all workers really start together
            barrier.wait()
            results.append(fn(db, index))
        except Exception as exc:  # noqa: BLE001 - collected for assertions
            results.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ------------------------------------------------------------------ payments

def test_fee_can_only_be_paid_once_even_concurrently(school, db):
    c, a = school.client, bearer(school.admin)
    fee_id = c.post("/fees", headers=a, json={"student_id": school.s1, "title": "T1", "amount": 900}).json()["id"]
    actor = _actor(db)
    methods = ["cash", "card", "upi", "netbanking", "wallet"]  # two clerks, two different receipts
    results = _run_concurrently(lambda d, i: fee_service.pay_fee(d, actor, fee_id, methods[i]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert all(isinstance(r, Conflict) for r in results if not isinstance(r, dict))
    assert db.scalar("SELECT COUNT(*) FROM transactions WHERE fee_id = %s", (fee_id,)) == 1


def test_claim_can_only_be_approved_once(school, db):
    c, a = school.client, bearer(school.admin)
    fee_id = c.post("/fees", headers=a, json={"student_id": school.s1, "title": "T1", "amount": 900}).json()["id"]
    student = bearer(login(c, "student1", "studpass"))
    claim = c.post(f"/fees/{fee_id}/claims", headers=student,
                   files={"file": ("p.png", png_bytes(), "image/png")}).json()
    first = c.put(f"/fees/claims/{claim['id']}/approve", headers=bearer(school.t1), json={})
    second = c.put(f"/fees/claims/{claim['id']}/approve", headers=a, json={})
    assert first.status_code == 200 and second.status_code == 409
    assert db.scalar("SELECT COUNT(*) FROM transactions WHERE fee_id = %s", (fee_id,)) == 1


def test_generating_class_fees_twice_never_duplicates(school, db):
    actor = _actor(db)
    from app.models.fees import FeeGenerate
    body = FeeGenerate(class_id=school.c1, title="Term 1")
    results = _run_concurrently(lambda d, _: fee_service.generate_class_fees(d, actor, body), n=4)
    assert sum(r["created"] for r in results) == 3       # three students in Class 1
    assert all(r["students"] == 3 for r in results)
    assert db.scalar("SELECT COUNT(*) FROM fees WHERE title = 'Term 1'") == 3


# ----------------------------------------------------------- partial updates

def test_student_update_keeps_fields_that_were_not_sent(school):
    c = school.client
    r = c.put(f"/students/{school.s1}", headers=bearer(school.t1), json={"full_name": "Renamed"})
    assert r.status_code == 200
    s = c.get(f"/students/{school.s1}", headers=bearer(school.admin)).json()
    assert s["full_name"] == "Renamed"
    assert s["father_name"] == "Father 1" and s["guardian_phone"] == "700000001" and s["address"] == "House 1"
    # explicitly sending null still clears a field
    c.put(f"/students/{school.s1}", headers=bearer(school.t1), json={"full_name": "Renamed", "address": None})
    assert c.get(f"/students/{school.s1}", headers=bearer(school.admin)).json()["address"] is None


def test_teacher_info_partial_update(school):
    c, a = school.client, bearer(school.admin)
    c.put(f"/users/teachers/{school.t1_id}", headers=a, json={"subject": "Math", "address": "Here"})
    c.put(f"/users/teachers/{school.t1_id}", headers=a, json={"qualification": "M.Ed"})
    t = next(t for t in c.get("/users/teachers", headers=a).json() if t["id"] == school.t1_id)
    assert (t["subject"], t["address"], t["qualification"]) == ("Math", "Here", "M.Ed")


# ------------------------------------------------------------------ timezone

def test_check_in_uses_school_time(school):
    r = school.client.post("/staff-attendance/check-in", headers=bearer(school.t1))
    assert r.status_code == 200
    stamp = datetime.strptime(r.json()["in_time"], "%H:%M:%S").time()
    now_ist = datetime.now(IST)
    delta = abs((datetime.combine(now_ist.date(), stamp) - now_ist.replace(tzinfo=None)).total_seconds())
    assert delta < 120 or delta > 86400 - 120
    today = school.client.get("/staff-attendance/today", headers=bearer(school.t1)).json()
    assert today["date"] == str(now_ist.date())
    assert school.client.post("/staff-attendance/check-in", headers=bearer(school.t1)).status_code == 409


def test_database_session_runs_in_school_timezone(db):
    assert db.scalar("SELECT @@session.time_zone") == "+05:30"
    assert db.scalar("SELECT CURDATE()") == datetime.now(IST).date()


# ------------------------------------------------------------------- files

def test_replaced_photo_is_deleted_after_commit(school):
    c, a = school.client, bearer(school.admin)
    first = c.post(f"/users/{school.t1_id}/photo", headers=a,
                   files={"file": ("a.jpg", jpeg_with_gps(400, 300), "image/jpeg")}).json()["photo_path"]
    first_disk = os.path.join(settings.upload_dir, first.split("?")[0].removeprefix("uploads/"))
    assert os.path.exists(first_disk)
    c.post(f"/users/{school.t1_id}/photo", headers=a, files={"file": ("b.png", png_bytes(), "image/png")})
    assert not os.path.exists(first_disk)


def test_failed_transaction_removes_the_new_file(db):
    from io import BytesIO

    from fastapi import UploadFile
    from starlette.datastructures import Headers

    upload = UploadFile(BytesIO(png_bytes()), filename="x.png", headers=Headers({"content-type": "image/png"}))
    saved = {}
    with pytest.raises(RuntimeError):
        with db.transaction():
            saved["path"] = storage.save_upload(db, upload, "photos", storage.IMAGE_TYPES).path
            assert os.path.exists(saved["path"])
            raise RuntimeError("simulated failure after saving the file")
    assert not os.path.exists(saved["path"])


# --------------------------------------------------------------------- misc

def test_bad_sms_group_is_a_400_not_a_500(client):
    r = client.post("/sms", headers=bearer(login(client, "admin")),
                    json={"recipient_group": "section:abc", "message": "hi"})
    assert r.status_code == 400 and r.json() == {"detail": "Unknown recipient group"}


def test_holiday_update_with_identical_values_is_not_a_404(client):
    a = bearer(login(client, "admin"))
    h = client.post("/holidays", headers=a, json={"date": "2026-12-25", "name": "Xmas"}).json()
    again = client.put(f"/holidays/{h['id']}", headers=a, json={"date": "2026-12-25", "name": "Xmas"})
    assert again.status_code == 200


def test_requests_survive_dropped_database_connections(client):
    """Simulates the database closing idle connections (e.g. after the app sat
    unused for hours, or a database restart)."""
    a = bearer(login(client, "admin"))
    assert client.get("/classes", headers=a).status_code == 200
    cfg = settings.db
    killer = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password)
    with killer.cursor() as cur:
        cur.execute("SELECT id FROM information_schema.processlist WHERE db = %s AND id <> CONNECTION_ID()",
                    (cfg.database,))
        for (pid,) in cur.fetchall():
            try:
                cur.execute(f"KILL {int(pid)}")
            except pymysql.MySQLError:
                pass
    killer.close()
    assert client.get("/classes", headers=a).status_code == 200


def _kill_idle_pool_connections() -> None:
    with pool._lock:
        thread_ids = [item.conn.thread_id() for item in pool._idle]
    cfg = settings.db
    killer = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password)
    with killer.cursor() as cur:
        for tid in thread_ids:
            cur.execute(f"KILL {int(tid)}")
    killer.close()


def test_pool_reconnects_without_ping(client, monkeypatch):
    """Even when the idle-ping is skipped, a pooled connection the server has
    closed is replaced transparently on first use."""
    from dataclasses import replace

    monkeypatch.setattr(pool, "_config", replace(pool._config, pool_ping_after_seconds=10_000))
    a = bearer(login(client, "admin"))
    client.get("/classes", headers=a)
    _kill_idle_pool_connections()
    assert client.get("/classes", headers=a).status_code == 200
    _kill_idle_pool_connections()  # also when the first statement opens a transaction
    assert client.post("/notices", headers=a, json={"title": "t", "body": "b"}).status_code == 201


def test_dashboard_week_has_seven_days(client):
    rows = client.get("/dashboard/attendance-week", headers=bearer(login(client, "admin"))).json()
    assert len(rows) == 7
    assert rows[-1]["date"] == str(datetime.now(IST).date())
    assert rows[0]["date"] == str(datetime.now(IST).date() - timedelta(days=6))
