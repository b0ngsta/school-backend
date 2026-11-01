"""Deleting a class, section, exam or fee never erases records (migration 0017).

Deletes that would erase attendance, homework, documents, substitutions,
lesson plans, marks, payments or payment proofs are refused with 409 and a
`code: has_records` + counts; everything else is deleted exactly as before."""
from __future__ import annotations

import pymysql
import pytest
from helpers import bearer, login, png_bytes

from app.core.clock import school_today


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json() if r.content else None


def _refused(r, *words):
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "has_records"
    for w in words:
        assert w in body["detail"], body["detail"]
    return body


def _audit(db, action):
    return db.all("SELECT entity_id, details FROM audit_logs WHERE action = %s", (action,))


# ------------------------------------------------------------------ sections

def test_section_with_attendance_is_kept_and_explains_why(school, db):
    c, a, t1 = school.client, bearer(school.admin), bearer(school.t1)
    _ok(c.post("/attendance", headers=t1, json={
        "section_id": school.c1a, "date": str(school_today()),
        "records": [{"student_id": school.s1, "status": "present"}, {"student_id": school.s2, "status": "absent"}]}))
    _ok(c.post("/homework", headers=bearer(school.t2), json={
        "class_id": school.c1, "section_id": school.c1a, "title": "Ex 1"}), 201)

    body = _refused(c.delete(f"/classes/{school.c1}/sections/{school.c1a}", headers=a),
                    "Section A can't be deleted", "2 attendance records and 1 homework post")
    assert body["records"] == {"attendance": 2, "homework": 1}
    # nothing changed: records, students, the section
    assert db.scalar("SELECT COUNT(*) FROM attendance WHERE section_id = %s", (school.c1a,)) == 2
    assert db.scalar("SELECT section_id FROM student_profiles WHERE user_id = %s", (school.s1,)) == school.c1a
    assert _audit(db, "section.delete") == []
    # the database refuses it too
    with pytest.raises(pymysql.err.IntegrityError):
        db.execute("DELETE FROM sections WHERE id = %s", (school.c1a,))


def test_section_without_records_is_deleted_as_before(school, db):
    c, a = school.client, bearer(school.admin)
    # section B has a student and nothing recorded yet: deleted, the student loses the section (as before)
    _ok(c.delete(f"/classes/{school.c1}/sections/{school.c1b}", headers=a))
    assert db.scalar("SELECT COUNT(*) FROM sections WHERE id = %s", (school.c1b,)) == 0
    assert db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s", (school.s3,)) == \
        {"class_id": school.c1, "section_id": None}
    [entry] = _audit(db, "section.delete")
    assert entry["entity_id"] == str(school.c1b) or entry["entity_id"] == school.c1b
    # a brand-new empty section too
    sec = _ok(c.post(f"/classes/{school.c2}/sections", headers=a, json={"name": "Z"}), 201)["id"]
    _ok(c.delete(f"/classes/{school.c2}/sections/{sec}", headers=a))
    assert c.delete(f"/classes/{school.c2}/sections/{sec}", headers=a).status_code == 404


# ------------------------------------------------------------------- classes

def test_class_with_marks_or_section_records_is_kept(school, db):
    c, a, t1 = school.client, bearer(school.admin), bearer(school.t1)
    exam = _ok(c.post("/exams", headers=a, json={"name": "Unit 1", "class_id": school.c1,
                                                 "start_date": "2026-09-01", "end_date": "2026-09-02"}), 201)
    _ok(c.put(f"/exams/{exam['id']}/results", headers=t1, json={
        "section_id": school.c1a, "results": [{"student_id": school.s1, "marks": 40}]}))
    body = _refused(c.delete(f"/classes/{school.c1}", headers=a), "Class 1 can't be deleted", "1 exam with results")
    assert body["records"] == {"exams_with_results": 1}
    assert db.scalar("SELECT COUNT(*) FROM exam_results WHERE exam_id = %s", (exam["id"],)) == 1
    assert db.scalar("SELECT COUNT(*) FROM sections WHERE class_id = %s", (school.c1,)) == 2
    with pytest.raises(pymysql.err.IntegrityError):  # cascade class → exam stops at the results
        db.execute("DELETE FROM classes WHERE id = %s", (school.c1,))

    _ok(c.post("/attendance", headers=bearer(school.t3), json={
        "section_id": school.c2a, "date": str(school_today()),
        "records": [{"student_id": school.s4, "status": "present"}]}))
    _refused(c.delete(f"/classes/{school.c2}", headers=a), "Class 2", "1 attendance record")


def test_class_without_records_is_deleted_as_before(school, db):
    c, a = school.client, bearer(school.admin)
    # Class 2: a section, a student, a class teacher, subjects and an exam with no results yet
    _ok(c.put(f"/classes/{school.c2}/subjects", headers=a, json={"subjects": ["Math"]}))
    exam = _ok(c.post("/exams", headers=a, json={"name": "Unit 1", "class_id": school.c2,
                                                 "start_date": "2026-09-01", "end_date": "2026-09-02"}), 201)
    _ok(c.delete(f"/classes/{school.c2}", headers=a))
    assert db.scalar("SELECT COUNT(*) FROM classes WHERE id = %s", (school.c2,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM sections WHERE id = %s", (school.c2a,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM exams WHERE id = %s", (exam["id"],)) == 0
    assert db.scalar("SELECT COUNT(*) FROM teacher_assignments WHERE class_id = %s", (school.c2,)) == 0
    assert db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s", (school.s4,)) == \
        {"class_id": None, "section_id": None}
    [entry] = _audit(db, "class.delete")
    assert '"Class 2"' in entry["details"] and '"students_unassigned": 1' in entry["details"]
    assert c.delete(f"/classes/{school.c2}", headers=a).status_code == 404


def test_only_managers_delete_classes(school):
    c = school.client
    assert c.delete(f"/classes/{school.c2}", headers=bearer(school.t3)).status_code == 403
    assert c.delete(f"/classes/{school.c2}/sections/{school.c2a}", headers=bearer(school.t3)).status_code == 403


# --------------------------------------------------------------------- exams

def test_exam_with_results_is_kept(school, db):
    c, a, t1 = school.client, bearer(school.admin), bearer(school.t1)
    exam = _ok(c.post("/exams", headers=a, json={"name": "Mid term", "class_id": school.c1,
                                                 "start_date": "2026-09-01", "end_date": "2026-09-10"}), 201)
    _ok(c.put(f"/exams/{exam['id']}/results", headers=t1, json={
        "section_id": school.c1a, "subject": "",
        "results": [{"student_id": school.s1, "marks": 40}, {"student_id": school.s2, "grade": "B"}]}))
    body = _refused(c.delete(f"/exams/{exam['id']}", headers=a), "results for 2 students", "unpublish")
    assert body["records"] == {"students_with_results": 2}
    assert db.scalar("SELECT COUNT(*) FROM exam_results WHERE exam_id = %s", (exam["id"],)) == 2
    with pytest.raises(pymysql.err.IntegrityError):
        db.execute("DELETE FROM exams WHERE id = %s", (exam["id"],))


def test_exam_without_results_is_deleted_as_before(school, db):
    c, a = school.client, bearer(school.admin)
    exam = _ok(c.post("/exams", headers=a, json={"name": "Typo exam", "class_id": school.c1,
                                                 "start_date": "2026-09-01", "end_date": "2026-09-10"}), 201)
    _ok(c.delete(f"/exams/{exam['id']}", headers=a))
    assert db.scalar("SELECT COUNT(*) FROM exams WHERE id = %s", (exam["id"],)) == 0


# ---------------------------------------------------------------------- fees

def test_paid_fee_and_fee_with_open_proof_are_kept(school, db):
    c, a = school.client, bearer(school.admin)
    paid = _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Term 1", "amount": 900}), 201)
    _ok(c.put(f"/fees/{paid['id']}/pay", headers=a, json={"method": "cash"}))
    _refused(c.delete(f"/fees/{paid['id']}", headers=a), "has been paid")
    assert db.one("SELECT fee_id FROM transactions WHERE student_id = %s", (school.s1,))["fee_id"] == paid["id"]

    claimed = _ok(c.post("/fees", headers=a, json={"student_id": school.s2, "title": "Bus", "amount": 300}), 201)
    student = bearer(login(c, "student2", "studpass"))
    claim = _ok(c.post(f"/fees/{claimed['id']}/claims", headers=student,
                       files={"file": ("p.png", png_bytes(), "image/png")}), 201)
    _refused(c.delete(f"/fees/{claimed['id']}", headers=a), "payment proof", "Approve or reject")
    with pytest.raises(pymysql.err.IntegrityError):
        db.execute("DELETE FROM fees WHERE id = %s", (claimed["id"],))

    # once the proof is rejected the fee can be deleted, as before (the rejected proof goes with it)
    _ok(c.put(f"/fees/claims/{claim['id']}/reject", headers=a, json={"note": "Blurry"}))
    _ok(c.delete(f"/fees/{claimed['id']}", headers=a))
    assert db.scalar("SELECT COUNT(*) FROM fees WHERE id = %s", (claimed["id"],)) == 0
    assert db.scalar("SELECT COUNT(*) FROM fee_claims WHERE id = %s", (claim["id"],)) == 0


def test_unpaid_fee_is_deleted_as_before(school, db):
    c, a = school.client, bearer(school.admin)
    fee = _ok(c.post("/fees", headers=a, json={"student_id": school.s1, "title": "Wrong amount", "amount": 1}), 201)
    _ok(c.delete(f"/fees/{fee['id']}", headers=a))
    assert db.scalar("SELECT COUNT(*) FROM fees WHERE id = %s", (fee["id"],)) == 0
    assert [r["entity_id"] for r in _audit(db, "fee.delete")] in ([fee["id"]], [str(fee["id"])])
