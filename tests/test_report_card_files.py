"""Report-card PDFs released to parents (migration 0028).

The office uploads the class saved as ONE PDF from the print window; the
server cuts it into one file per student and parents download theirs.
"""
from __future__ import annotations

import io
from datetime import timedelta

import pikepdf
from helpers import bearer, login, pdf_pages

from app.core.clock import school_today
from app.services import school_profile


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def _exam(client, a):
    return _ok(client.post("/exams", headers=a, json={
        "name": "Half-yearly", "start_date": str(school_today()),
        "end_date": str(school_today() + timedelta(days=5))}), 201)["id"]


def _release(client, headers, students, pdf, *, source="exam", exam_id=None, sides=2, code=201, **extra):
    data = {"source": source, "title": extra.pop("title", "Half-yearly 2026"), "sides": str(sides),
            "students": str(list(students))}
    if exam_id is not None:
        data["exam_id"] = str(exam_id)
    data.update({k: str(v) for k, v in extra.items()})
    return _ok(client.post("/report-card-releases", headers=headers, data=data,
                           files={"file": ("class.pdf", pdf, "application/pdf")}), code)


def _pages_text(client, path) -> list[str]:
    r = client.get("/" + path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf"), r.text
    with pikepdf.open(io.BytesIO(r.content)) as pdf:
        return [bytes(page.Contents.read_bytes()).decode() for page in pdf.pages]


def test_office_releases_a_class_and_each_parent_gets_their_own_pages(client, school):
    a = bearer(school.admin)
    exam = _exam(client, a)
    # print order: student 2 first, then student 1 (front + back each)
    out = _release(client, a, [school.s2, school.s1], pdf_pages(4), exam_id=exam,
                   page_w_mm="210", page_h_mm="297")
    assert (out["released"], out["new"], out["replaced"]) == (2, 2, 0)
    assert out["result_key"] == f"exam:{exam}" and out["bytes"] > 0
    files = {f["student_id"]: f for f in out["files"]}
    assert files[school.s1]["pages"] == 2 and files[school.s1]["label"] == "Class 1 — A"
    assert files[school.s1]["attachment_type"] == "pdf"
    assert files[school.s1]["attachment_name"] == "Report card - Student 1 - Half-yearly 2026.pdf"
    assert files[school.s1]["attachment_path"].startswith("uploads/report-cards/")
    assert "sig=" in files[school.s1]["attachment_path"]

    # each parent: only their child's file, with exactly their pages
    p1 = bearer(login(client, "student1", "studpass"))
    p2 = bearer(login(client, "student2", "studpass"))
    mine = _ok(client.get("/student/report-card-files", headers=p1))
    assert [m["student_id"] for m in mine] == [school.s1]
    assert [t.split("(")[1].split(")")[0] for t in _pages_text(client, mine[0]["attachment_path"])] == \
        ["Page 3", "Page 4"]
    theirs = _ok(client.get("/student/report-card-files", headers=p2))
    assert [t.split("(")[1].split(")")[0] for t in _pages_text(client, theirs[0]["attachment_path"])] == \
        ["Page 1", "Page 2"]
    assert _ok(client.get("/student/report-card-files", headers=bearer(login(client, "student3", "studpass")))) == []

    # parents are told, and the Performance Report's red dot lights up
    inbox = _ok(client.get("/notifications", headers=p1))
    note = next(n for n in inbox if n["type"] == "report_card")
    assert note["title"] == "📄 Report card ready" and note["data"]["route"] == "ReportCards"
    assert note["data"]["params"]["report_card_id"] == files[school.s1]["id"]
    assert _ok(client.get("/updates", headers=p1))["sections"]["report_cards"]["latest_at"]

    # staff views
    assert len(_ok(client.get(f"/report-card-files?exam_id={exam}", headers=a))) == 2
    summary = _ok(client.get("/report-card-releases", headers=a))
    assert [(s["result_key"], s["students"], s["exam_name"]) for s in summary] == [(f"exam:{exam}", 2, "Half-yearly")]
    t1 = bearer(school.t1)  # class teacher of 1-A sees the student's files, cannot release
    assert len(_ok(client.get(f"/students/{school.s1}/report-card-files", headers=t1))) == 1
    _ok(client.get(f"/students/{school.s4}/report-card-files", headers=t1), 403)
    _release(client, t1, [school.s1], pdf_pages(2), exam_id=exam, code=403)
    _ok(client.get("/report-card-files", headers=t1), 403)
    _ok(client.get("/report-card-releases", headers=p1), 403)


def test_releasing_again_replaces_the_file(client, school):
    a = bearer(school.admin)
    exam = _exam(client, a)
    first = _release(client, a, [school.s1], pdf_pages(2), exam_id=exam)["files"][0]
    again = _release(client, a, [school.s1, school.s2], pdf_pages(4), exam_id=exam)
    assert (again["new"], again["replaced"]) == (1, 1)
    now = next(f for f in again["files"] if f["student_id"] == school.s1)
    assert now["id"] == first["id"] and now["attachment_path"] != first["attachment_path"]
    assert client.get("/" + first["attachment_path"]).status_code == 404  # old file removed
    p1 = bearer(login(client, "student1", "studpass"))
    titles = [n["title"] for n in _ok(client.get("/notifications", headers=p1)) if n["type"] == "report_card"]
    assert sorted(titles) == ["📄 Report card ready", "📄 Report card updated"]

    # notify=false sends nothing; a final result is its own release
    final = _release(client, a, [school.s1], pdf_pages(1), source="final", sides=1, notify="false")
    session = school_profile.current_session(_db_for_test())["label"]
    assert final["result_key"] == f"final:{session}" and final["files"][0]["session"] == session
    assert len([n for n in _ok(client.get("/notifications", headers=p1)) if n["type"] == "report_card"]) == 2
    assert len(_ok(client.get("/student/report-card-files", headers=p1))) == 2

    # withdraw one file, then a whole release
    _ok(client.delete(f"/report-card-files/{final['files'][0]['id']}", headers=a), 204)
    assert len(_ok(client.get("/student/report-card-files", headers=p1))) == 1
    assert _ok(client.delete("/report-card-releases", headers=a, params={"result_key": f"exam:{exam}"})) == \
        {"withdrawn": 2}
    assert _ok(client.get("/student/report-card-files", headers=p1)) == []
    _ok(client.delete("/report-card-releases", headers=a, params={"result_key": f"exam:{exam}"}), 404)
    _ok(client.delete("/report-card-files/999999", headers=a), 404)


def _db_for_test():
    from app.db import Db, pool

    db = Db(pool)
    db.bind_org(1)
    return db


def test_wrong_files_are_refused_and_nothing_is_stored(client, school):
    a = bearer(school.admin)
    exam = _exam(client, a)
    r = _release(client, a, [school.s1, school.s2], pdf_pages(3), exam_id=exam, code=400)
    assert "has 3 pages, but 2 students × 2 sides = 4 pages" in r["detail"]
    r = _release(client, a, [school.s1], pdf_pages(2, 842, 595), exam_id=exam, code=400,
                 page_w_mm="210", page_h_mm="297")
    assert "Page 1 is 297 × 210 mm" in r["detail"]
    _release(client, a, [school.s1], b"not a pdf at all", exam_id=exam, code=415)
    _release(client, a, [school.s1], b"%PDF-1.4 broken", exam_id=exam, code=415)
    _release(client, a, [school.s1, school.s1], pdf_pages(4), exam_id=exam, code=400)
    _release(client, a, [], pdf_pages(2), exam_id=exam, code=400)
    _release(client, a, [school.s1], pdf_pages(2), code=400)                  # exam without exam_id
    _release(client, a, [school.s1], pdf_pages(2), exam_id=999999, code=404)
    _release(client, a, [school.t1_id], pdf_pages(2), exam_id=exam, code=404)  # a teacher is not a student
    _release(client, a, [school.s1], pdf_pages(2), exam_id=exam, front_template_id=999999, code=404)
    _release(client, a, [school.s1], pdf_pages(2), exam_id=exam, sides=3, code=422)
    _release(client, a, [school.s1], pdf_pages(2), exam_id=exam, title="   ", code=400)
    assert _ok(client.get("/report-card-files", headers=a)) == []
    assert _ok(client.get("/report-card-releases", headers=a)) == []
