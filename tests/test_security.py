"""Uploads, signed file links, access control and hardening."""
import os
from io import BytesIO

import pytest
from helpers import PDF_BYTES, bearer, jpeg_with_gps, login, png_bytes
from PIL import Image

from app.core import config


def _photo_upload(client, headers, user_id, data, name="me.jpg", ctype="image/jpeg"):
    return client.post(f"/users/{user_id}/photo", headers=headers, files={"file": (name, data, ctype)})


# ------------------------------------------------------------------ uploads

def test_photo_is_reencoded_without_gps_and_resized(school):
    r = _photo_upload(school.client, bearer(school.admin), school.t1_id, jpeg_with_gps())
    assert r.status_code == 200
    link = r.json()["photo_path"]
    assert link.startswith("uploads/photos/") and "?exp=" in link and "&sig=" in link

    img = school.client.get("/" + link)
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    assert img.headers["x-content-type-options"] == "nosniff"
    stored = Image.open(BytesIO(img.content))
    assert max(stored.size) == 2560
    assert dict(stored.getexif()) == {}  # no camera model, no GPS


@pytest.mark.parametrize("name,data,ctype,status", [
    ("evil.html", b"<script>alert(1)</script>", "image/jpeg", 415),     # html disguised as a photo
    ("x.jpg", b"\xff\xd8\xff" + b"garbage" * 10, "image/jpeg", 415),     # corrupt image
    ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml", 415),           # type not allowed
    ("x.pdf", PDF_BYTES, "application/pdf", 415),                        # pdf where images are required
])
def test_bad_uploads_are_rejected(school, name, data, ctype, status):
    r = _photo_upload(school.client, bearer(school.admin), school.t1_id, data, name, ctype)
    assert r.status_code == status, r.text


def test_upload_size_limit(school):
    big = b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024 + 10)
    r = _photo_upload(school.client, bearer(school.admin), school.t1_id, big)
    assert r.status_code == 413 and r.json() == {"detail": "Max 5 MB"}


def test_server_chooses_the_extension(school):
    r = _photo_upload(school.client, bearer(school.admin), school.t1_id, png_bytes(), "kid.html", "image/png")
    assert r.status_code == 200
    assert r.json()["photo_path"].split("?")[0].endswith(".png")


# ------------------------------------------------------------- signed links

def test_files_are_not_public(school):
    link = _photo_upload(school.client, bearer(school.admin), school.t1_id, png_bytes(), "a.png",
                         "image/png").json()["photo_path"]
    path, query = link.split("?")
    c = school.client
    assert c.get("/" + path).status_code == 403                                   # no signature
    assert c.get("/" + link.replace("sig=", "sig=x")).status_code == 403          # tampered
    assert c.get("/" + link.replace("photos/", "claims/")).status_code == 403     # other file
    expired = "exp=1000&" + query.split("&", 1)[1]
    assert c.get(f"/{path}?{expired}").status_code == 403
    assert c.get("/uploads/../../etc/passwd?exp=99999999999&sig=x").status_code in (403, 404)


def test_exam_papers_need_staff_login(school):
    c, a = school.client, bearer(school.admin)
    exam = c.post("/exams", headers=a, json={"name": "UT", "class_id": school.c1,
                                             "start_date": "2026-01-01", "end_date": "2026-01-02"}).json()
    up = c.post(f"/exams/{exam['id']}/classes/{school.c1}/papers", headers=a, data={"subject": "Math"},
                files={"file": ("m.pdf", PDF_BYTES, "application/pdf")})
    assert up.status_code == 201
    paper = c.get(f"/exams/{exam['id']}/classes/{school.c1}/papers", headers=a).json()["subjects"][0]["paper"]
    assert "sig=" in paper["paper_path"]
    student = login(c, "student1", "studpass")
    assert c.get(f"/exams/{exam['id']}/papers/{paper['id']}/file", headers=bearer(student)).status_code == 403
    pdf = c.get(f"/exams/{exam['id']}/papers/{paper['id']}/file", headers=bearer(school.t1))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"


# ------------------------------------------------------- cross-section access

def test_teacher_cannot_mark_attendance_for_another_section(school):
    r = school.client.post("/attendance", headers=bearer(school.t1), json={
        "section_id": school.c1a, "date": "2026-10-01",
        "records": [{"student_id": school.s1, "status": "present"},
                    {"student_id": school.s4, "status": "absent"}]})  # s4 is in Class 2-A
    assert r.status_code == 400
    assert str(school.s4) in r.json()["detail"]
    marked = school.client.get(f"/attendance?section_id={school.c1a}&day=2026-10-01",
                               headers=bearer(school.t1)).json()
    assert all(row["status"] is None for row in marked)  # nothing was written


def test_teacher_cannot_write_marks_for_another_section(school):
    c = school.client
    exam = c.post("/exams", headers=bearer(school.admin), json={
        "name": "All", "start_date": "2026-01-01", "end_date": "2026-01-02"}).json()
    r = c.put(f"/exams/{exam['id']}/results", headers=bearer(school.t2), json={
        "section_id": school.c1a, "results": [{"student_id": school.s4, "marks": 100}]})
    assert r.status_code == 400
    ok = c.put(f"/exams/{exam['id']}/results", headers=bearer(school.t2), json={
        "section_id": school.c1a, "results": [{"student_id": school.s1, "marks": 90}]})
    assert ok.status_code == 200


def test_class_teacher_cannot_move_student_out_of_their_section(school):
    r = school.client.put(f"/students/{school.s1}", headers=bearer(school.t1), json={
        "full_name": "Student 1", "class_id": school.c2, "section_id": school.c2a})
    assert r.status_code == 403
    assert school.client.get(f"/students/{school.s1}", headers=bearer(school.admin)).json()["class_id"] == school.c1


def test_section_must_belong_to_class_when_moving(school):
    r = school.client.put(f"/students/{school.s1}", headers=bearer(school.admin), json={
        "full_name": "Student 1", "class_id": school.c2, "section_id": school.c1b})
    assert r.status_code == 404 and r.json() == {"detail": "Section not found in that class"}
    moved = school.client.put(f"/students/{school.s1}", headers=bearer(school.admin), json={
        "full_name": "Student 1", "class_id": school.c2, "section_id": school.c2a})
    assert moved.status_code == 200


def test_account_deletion_mirrors_creation_rules(client):
    admin = bearer(login(client, "admin"))
    sub = bearer(login(client, "subadmin"))
    other = client.post("/users", headers=admin, json={
        "username": "subadmin2", "password": "secret1", "full_name": "S2", "user_type": "sub_admin"}).json()
    coord = client.post("/users", headers=sub, json={
        "username": "coord9", "password": "secret1", "full_name": "C9", "user_type": "coordinator"}).json()
    assert client.delete(f"/users/{other['id']}", headers=sub).status_code == 403
    assert client.delete(f"/users/{coord['id']}", headers=sub).status_code == 200
    assert client.delete(f"/users/{other['id']}", headers=admin).status_code == 200


def test_students_cannot_reach_staff_endpoints(school):
    student = bearer(login(school.client, "student1", "studpass"))
    for path in ("/students", "/fees", "/users", "/fees/claims", "/exams"):
        assert school.client.get(path, headers=student).status_code == 403


# ---------------------------------------------------------------- hardening

def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-request-id"]


def test_errors_keep_the_detail_shape(client):
    r = client.get("/students/1", headers=bearer(login(client, "admin")))
    assert r.status_code == 404 and r.json() == {"detail": "Student not found"}


def test_production_refuses_weak_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "change-me-in-production")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.load_settings()


def test_production_defaults_are_locked_down(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "a" * 64)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    s = config.load_settings()
    assert s.cors_origins == () and s.enable_docs is False


def test_audit_log_records_sensitive_actions(school, db):
    c, a = school.client, bearer(school.admin)
    fee = c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Bus", "amount": 100}).json()
    c.put(f"/fees/{fee['id']}/pay", headers=a, json={"method": "upi"})
    actions = [r["action"] for r in db.all("SELECT action FROM audit_logs ORDER BY id")]
    assert {"student.create", "fee.create", "fee.pay"} <= set(actions)


def test_upload_dir_is_outside_web_root(school):
    # nothing under the upload folder is reachable without a signature
    for root, _, files in os.walk(config.settings.upload_dir):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), config.settings.upload_dir)
            assert school.client.get(f"/uploads/{rel}").status_code == 403
