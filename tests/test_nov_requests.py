"""School requests of Nov 2026 (migration 0021): classes with a section count /
without sections, the office sets passwords, teacher profile, attendance
history, fees (undo paid, overdue list, payment history, class fee in parts),
exam format + grading system + final result.

School (helpers.School): Class 1 — A (student1, student2; teacher1 class teacher,
teacher2 Math), Class 1 — B (student3), Class 2 — A (student4; teacher3 class teacher).
Class 1's fee is 5000, Class 2's 6000.
"""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from helpers import bearer, login
from openpyxl import Workbook, load_workbook

from app.core.clock import school_today


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


# ---------------------------------------------------------------- classes

def test_class_with_a_section_count_and_class_without_sections(client, school):
    a = bearer(school.admin)
    five = _ok(client.post("/classes", headers=a, json={"name": "Class 5", "sections": 4}), 201)
    assert [s["name"] for s in five["sections"]] == ["A", "B", "C", "D"] and five["has_sections"] == 1
    _ok(client.post("/classes", headers=a, json={"name": "Class 9", "sections": 27}), 422)
    plain = _ok(client.post("/classes", headers=a, json={"name": "Class 7"}), 201)  # older apps: none yet
    assert plain["sections"] == []

    six = _ok(client.post("/classes", headers=a, json={"name": "Class 6", "sections": 0, "fee_amount": 100}), 201)
    assert six["has_sections"] == 0 and [s["name"] for s in six["sections"]] == [""]
    whole = six["sections"][0]["id"]
    listed = {c["name"]: c for c in _ok(client.get("/classes", headers=a))}
    assert listed["Class 6"]["has_sections"] == 0 and len(listed["Class 6"]["sections"]) == 1
    assert [s["name"] for s in listed["Class 5"]["sections"]] == ["A", "B", "C", "D"]
    assert listed["Class 1"]["has_sections"] == 1

    # the whole class works exactly like a section: students, class teacher, attendance, labels
    kid = _ok(client.post("/students", headers=a, json={
        "username": "six1", "password": "studpass", "full_name": "Six One", "roll_no": "1",
        "class_id": six["id"], "section_id": whole}), 201)["id"]
    _ok(client.post("/assignments", headers=a, json={"teacher_id": school.t2_id, "class_id": six["id"],
                                                     "section_id": whole, "role": "class_teacher"}), 201)
    t2 = bearer(school.t2)
    _ok(client.post("/attendance", headers=t2, json={"section_id": whole, "date": str(school_today()),
                                                     "records": [{"student_id": kid, "status": "present"}]}))
    assert _ok(client.get(f"/attendance/history?section_id={whole}", headers=t2))["marked_today"] is True
    targets = _ok(client.get("/class-announcements/targets", headers=t2))
    assert "Class 6" in [t["label"] for t in targets]  # no "— " after the class name
    ann = _ok(client.post("/class-announcements", headers=t2,
                          json={"title": "Hi", "body": "x", "section_ids": [whole]}), 201)
    assert _ok(client.get(f"/class-announcements/{ann['id']}", headers=t2))["audience_label"] == "Class 6"

    # the hidden section can't be deleted; the first named section takes it over
    _ok(client.delete(f"/classes/{six['id']}/sections/{whole}", headers=a), 400)
    named = _ok(client.post(f"/classes/{six['id']}/sections", headers=a, json={"name": "A"}), 201)
    assert named["id"] == whole and named["renamed"] is True
    more = _ok(client.post(f"/classes/{six['id']}/sections", headers=a, json={"name": "B"}), 201)
    assert more["id"] != whole
    after = {c["name"]: c for c in _ok(client.get("/classes", headers=a))}["Class 6"]
    assert after["has_sections"] == 1 and [s["name"] for s in after["sections"]] == ["A", "B"]
    assert _ok(client.get(f"/students/{kid}", headers=a))["section_name"] == "A"


# ---------------------------------------------------------------- users

def test_the_office_sets_passwords(client, school):
    coord = bearer(login(client, "coordinator"))
    a = bearer(school.admin)
    path = f"/users/{school.t1_id}/password"
    _ok(client.put(path, headers=bearer(school.t2), json={"new_password": "newpass1"}), 403)
    _ok(client.put(path, headers=coord, json={"new_password": "abc"}), 422)
    done = _ok(client.put(path, headers=coord, json={"new_password": "newpass1", "must_change_password": False}))
    assert done["username"] == "teacher1" and done["sessions_revoked"] is True
    _ok(client.get("/auth/me", headers=bearer(school.t1)), 401)  # signed out everywhere
    assert client.post("/auth/login", json={"username": "teacher1", "password": "teachpass"}).status_code == 401
    again = login(client, "teacher1", "newpass1")
    assert not again["must_change_password"]

    # by default the person must choose their own password at the next sign-in
    _ok(client.put(f"/users/{school.s1}/password", headers=coord, json={"new_password": "parent99"}))
    assert login(client, "student1", "parent99")["must_change_password"]

    # admin / principal / sub-admin accounts: admin only; never your own; unknown; left accounts
    principal = next(u for u in _ok(client.get("/users?user_type=principal", headers=a)))["id"]
    _ok(client.put(f"/users/{principal}/password", headers=coord, json={"new_password": "newpass1"}), 403)
    _ok(client.put(f"/users/{principal}/password", headers=a, json={"new_password": "newpass1"}))
    me = _ok(client.get("/auth/me", headers=a))["id"]
    _ok(client.put(f"/users/{me}/password", headers=a, json={"new_password": "newpass1"}), 400)
    _ok(client.put("/users/999999/password", headers=a, json={"new_password": "newpass1"}), 404)
    _ok(client.delete(f"/users/{school.t3_id}", headers=a))
    _ok(client.put(f"/users/{school.t3_id}/password", headers=a, json={"new_password": "newpass1"}), 409)


def test_one_teacher_for_the_profile_page(client, school):
    a = bearer(school.admin)
    _ok(client.put(f"/users/teachers/{school.t1_id}", headers=a, json={"designation": "TGT", "subject": "English"}))
    t = _ok(client.get(f"/users/teachers/{school.t1_id}", headers=a))
    assert (t["full_name"], t["designation"], t["subject"]) == ("Teacher 1", "TGT", "English")
    assert t["user_type"] == "teacher"
    assert [(x["class_name"], x["section_name"], x["role"]) for x in t["assignments"]] == [
        ("Class 1", "A", "class_teacher")]
    _ok(client.get(f"/users/teachers/{school.s1}", headers=a), 404)
    _ok(client.get(f"/users/teachers/{school.t1_id}", headers=bearer(school.t1)), 403)


# ---------------------------------------------------------------- attendance

def test_attendance_history_of_a_section(client, school):
    t1, today = bearer(school.t1), school_today()
    for day, status in ((today - timedelta(days=1), "absent"), (today, "half_day")):
        _ok(client.post("/attendance", headers=t1, json={"section_id": school.c1a, "date": str(day), "records": [
            {"student_id": school.s1, "status": "present"}, {"student_id": school.s2, "status": status}]}))
    h = _ok(client.get(f"/attendance/history?section_id={school.c1a}", headers=t1))
    assert h["can_mark"] is True and h["marked_today"] is True and h["students"] == 2
    assert [d["date"] for d in h["days"]] == [str(today), str(today - timedelta(days=1))]
    assert (h["days"][0]["present"], h["days"][0]["half_day"], h["days"][0]["total"]) == (1, 1, 2)
    assert h["days"][0]["percentage"] == 75.0 and h["days"][0]["marked_by_name"] == "Teacher 1"
    assert (h["days"][1]["absent"], h["days"][1]["percentage"]) == (1, 50.0)
    # the subject teacher sees it but can't mark; another class's teacher doesn't see it
    assert _ok(client.get(f"/attendance/history?section_id={school.c1a}", headers=bearer(school.t2)))["can_mark"] \
        is False
    _ok(client.get(f"/attendance/history?section_id={school.c1a}", headers=bearer(school.t3)), 403)
    empty = _ok(client.get(f"/attendance/history?section_id={school.c1b}&days=7", headers=bearer(school.admin)))
    assert empty["days"] == [] and empty["marked_today"] is False and empty["can_mark"] is True
    _ok(client.get("/attendance/history?section_id=999999", headers=bearer(school.admin)), 404)


# ---------------------------------------------------------------- fees

def test_a_paid_fee_can_be_marked_unpaid_again(client, school):
    a = bearer(school.admin)
    fee = _ok(client.post("/fees", headers=a, json={"student_id": school.s1, "title": "Bus", "amount": 800}), 201)["id"]
    _ok(client.put(f"/fees/{fee}/unpay", headers=a, json={}), 409)  # not paid yet
    _ok(client.put(f"/fees/{fee}/pay", headers=a, json={"method": "cash"}))
    assert _ok(client.get("/transactions/stats", headers=a))["month_collection"] == 800
    _ok(client.put(f"/fees/{fee}/unpay", headers=bearer(school.t1), json={}), 403)
    undone = _ok(client.put(f"/fees/{fee}/unpay", headers=a, json={"reason": "Wrong student"}))
    assert undone == {"unpaid": fee, "transactions_reversed": 1}
    row = _ok(client.get(f"/fees?student_id={school.s1}", headers=a))[0]
    assert (row["status"], row["paid_date"], row["method"]) == ("pending", None, None)
    # the payment is kept, marked reversed, and no longer counted
    txns = _ok(client.get("/transactions", headers=a))
    assert [(t["fee_id"], t["status"]) for t in txns] == [(fee, "reversed")]
    assert _ok(client.get("/transactions/stats", headers=a))["month_collection"] == 0
    assert _ok(client.get("/fees/stats", headers=a))["collected"] == 0
    kid = bearer(login(client, "student1", "studpass"))
    assert _ok(client.get("/student/fees", headers=kid))["records"][0]["status"] == "pending"
    told = [n["title"] for n in _ok(client.get("/notifications", headers=kid))]
    assert any(t.startswith("Payment undone") for t in told)
    _ok(client.put(f"/fees/{fee}/unpay", headers=a), 409)
    # it can be paid again (a new payment) and, unpaid, deleted like any unpaid fee
    _ok(client.put(f"/fees/{fee}/pay", headers=a, json={"method": "upi"}))
    assert sorted(t["status"] for t in _ok(client.get("/transactions", headers=a))) == ["reversed", "success"]
    _ok(client.put(f"/fees/{fee}/unpay", headers=a))
    _ok(client.put("/fees/999999/unpay", headers=a), 404)


def test_overdue_students_and_a_students_payment_history(client, school):
    a, today = bearer(school.admin), school_today()
    late, soon = str(today - timedelta(days=10)), str(today + timedelta(days=10))

    def fee(student, title, amount, due):
        return _ok(client.post("/fees", headers=a, json={"student_id": student, "title": title, "amount": amount,
                                                         "due_date": due}), 201)["id"]

    f1 = fee(school.s1, "April", 1000, late)
    fee(school.s1, "May", 500, str(today - timedelta(days=3)))
    fee(school.s1, "June", 700, soon)
    fee(school.s3, "April", 2000, late)
    fee(school.s4, "April", 900, soon)  # not due yet
    paid = fee(school.s2, "April", 400, late)
    _ok(client.put(f"/fees/{paid}/pay", headers=a, json={"method": "cash"}))  # paid: not overdue

    r = client.get("/fees/overdue-students?limit=10", headers=a)
    rows = _ok(r)
    assert r.headers["X-Total-Count"] == "2"
    assert [(x["student_id"], float(x["overdue_total"]), x["overdue_count"]) for x in rows] == [
        (school.s3, 2000, 1), (school.s1, 1500, 2)]
    assert rows[1]["oldest_due"] == late and rows[1]["class_name"] == "Class 1"
    _ok(client.get("/fees/overdue-students", headers=bearer(school.t1)), 403)

    _ok(client.put(f"/fees/{f1}/pay", headers=a, json={"method": "upi"}))
    h = _ok(client.get(f"/fees/students/{school.s1}/history", headers=a))
    assert h["student"]["full_name"] == "Student 1" and h["student"]["section_name"] == "A"
    assert h["totals"] == {"paid": 1000.0, "pending": 700.0, "overdue": 500.0, "billed": 2200.0}
    assert {f["title"]: f["effective_status"] for f in h["fees"]} == {
        "April": "paid", "May": "overdue", "June": "pending"}
    assert [(p["fee_title"], p["method"], p["status"]) for p in h["payments"]] == [("April", "upi", "success")]
    _ok(client.get("/fees/students/999999/history", headers=a), 404)
    _ok(client.get(f"/fees/students/{school.t1_id}/history", headers=a), 404)


def test_the_class_fee_is_billed_in_parts(client, school):
    a = bearer(school.admin)

    def generate(title, amount, code=201, class_id=None):
        return _ok(client.post("/fees/generate", headers=a, json={
            "class_id": class_id or school.c1, "title": title, "amount": amount}), code)

    plan = _ok(client.get(f"/fees/class-plan?class_id={school.c1}", headers=a))
    assert (plan["class_fee"], plan["allotted"], plan["remaining"], plan["installments"]) == (5000, 0, 5000, [])
    first = generate("First installment", 2000)
    assert (first["created"], first["students"], first["amount"]) == (3, 3, 2000)
    assert first["plan"]["remaining"] == 3000
    fees = _ok(client.get(f"/fees?student_id={school.s1}", headers=a))
    assert [(f["title"], float(f["amount"])) for f in fees] == [("First installment", 2000)]

    # never more than the class's fee; the same title keeps its amount
    over = client.post("/fees/generate", headers=a, json={"class_id": school.c1, "title": "Second", "amount": 3500})
    assert over.status_code == 400 and over.json()["code"] == "over_class_fee" and over.json()["remaining"] == 3000
    generate("First installment", 1500, 409)
    assert generate("First installment", 2000)["created"] == 0  # nobody new
    second = generate("Second", 3000)
    assert second["plan"]["remaining"] == 0 and len(second["plan"]["installments"]) == 2
    assert generate("Third", 1, 400)["code"] == "over_class_fee"

    # a student who joins later gets the parts when they are generated again
    _ok(client.post("/students", headers=a, json={"username": "late1", "password": "studpass", "full_name": "Late",
                                                  "class_id": school.c1, "section_id": school.c1b}), 201)
    assert generate("First installment", 2000)["created"] == 1

    # a part billed by mistake can be taken back while nobody has paid it
    second_id = second["installment_id"]
    gone = _ok(client.delete(f"/fees/installments/{second_id}", headers=a))
    assert gone["fees_removed"] == 3
    plan = _ok(client.get(f"/fees/class-plan?class_id={school.c1}", headers=a))
    assert plan["remaining"] == 3000 and [(p["title"], p["students"], p["paid"]) for p in plan["installments"]] == [
        ("First installment", 4, 0)]
    mine = _ok(client.get(f"/fees?student_id={school.s1}", headers=a))
    _ok(client.put(f"/fees/{mine[0]['id']}/pay", headers=a, json={"method": "cash"}))
    kept = client.delete(f"/fees/installments/{first['installment_id']}", headers=a)
    assert kept.status_code == 409 and kept.json()["records"] == {"paid": 1}
    _ok(client.delete("/fees/installments/999999", headers=a), 404)
    _ok(client.get(f"/fees/class-plan?class_id={school.c1}", headers=bearer(school.t1)), 403)

    # the class fee itself can be changed later — but not below what is already billed in parts
    low = client.put(f"/classes/{school.c1}", headers=a, json={"fee_amount": 1500})
    assert low.status_code == 400 and low.json()["code"] == "below_billed" and low.json()["allotted"] == 2000
    raised = _ok(client.put(f"/classes/{school.c1}", headers=a, json={"fee_amount": 8000}))
    assert float(raised["fee_amount"]) == 8000 and raised["name"] == "Class 1"
    assert _ok(client.get(f"/fees/class-plan?class_id={school.c1}", headers=a))["remaining"] == 6000
    _ok(client.put(f"/classes/{school.c1}", headers=a, json={"name": "Class 2"}), 409)
    assert _ok(client.put(f"/classes/{school.c1}", headers=a, json={"name": "Class One"}))["name"] == "Class One"
    _ok(client.put(f"/classes/{school.c1}", headers=a, json={"name": "Class 1"}))
    _ok(client.put(f"/classes/{school.c1}", headers=bearer(school.t1), json={"fee_amount": 1}), 403)
    _ok(client.put("/classes/999999", headers=a, json={"fee_amount": 1}), 404)

    # without an amount (older apps) the whole class fee is billed, as before
    old = _ok(client.post("/fees/generate", headers=a, json={"class_id": school.c2, "title": "Annual"}), 201)
    assert (old["created"], old["amount"]) == (1, 6000) and "installment_id" not in old
    assert _ok(client.get(f"/fees/class-plan?class_id={school.c2}", headers=a))["allotted"] == 0


# ---------------------------------------------------------------- exam format, grading, final result

BANDS = [{"grade": "E", "min_percent": 0, "max_percent": 20}, {"grade": "D", "min_percent": 20, "max_percent": 40},
         {"grade": "C", "min_percent": 40, "max_percent": 60}, {"grade": "B", "min_percent": 60, "max_percent": 80},
         {"grade": "A", "min_percent": 80, "max_percent": 100}]
PARTS = [{"name": "Notebook", "max_marks": 5}, {"name": "Handwriting", "max_marks": 5},
         {"name": "Term 1", "max_marks": 20}, {"name": "Final exam", "max_marks": 70}]


def test_the_school_sets_its_grading_system(client, school):
    a, coord = bearer(school.admin), bearer(login(client, "coordinator"))
    default = _ok(client.get("/grading", headers=bearer(school.t1)))
    assert default["custom"] is False and default["bands"][0] == {"grade": "A1", "min_percent": 91.0,
                                                                  "max_percent": 100.0}
    _ok(client.put("/grading", headers=bearer(school.t1), json={"bands": BANDS}), 403)
    saved = _ok(client.put("/grading", headers=coord, json={"bands": BANDS}))
    assert saved["custom"] is True and [b["grade"] for b in saved["bands"]] == ["A", "B", "C", "D", "E"]
    bad = [dict(BANDS[0], max_percent=30), *BANDS[1:]]  # E 0–30 overlaps D 20–40
    assert "overlaps" in _ok(client.put("/grading", headers=a, json={"bands": bad}), 400)["detail"]
    assert "start from 0" in _ok(client.put("/grading", headers=a, json={"bands": BANDS[1:]}), 400)["detail"]
    _ok(client.put("/grading", headers=a, json={"bands": [BANDS[0], BANDS[0]]}), 400)
    assert _ok(client.get("/grading", headers=a))["custom"] is True  # failed saves changed nothing
    assert _ok(client.put("/grading", headers=a, json={"bands": []}))["custom"] is False  # back to the default


def _marks_file(header: list[str], rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def test_exam_format_and_final_result(client, school):
    a, t1 = bearer(school.admin), bearer(school.t1)
    _ok(client.put(f"/classes/{school.c1}/subjects", headers=a, json={"subjects": ["English", "Maths"]}))
    _ok(client.put("/grading", headers=a, json={"bands": BANDS}))

    # ---- the office lists the session's exams of the class with their marks
    _ok(client.put("/exam-format", headers=t1, json={"class_id": school.c1, "parts": PARTS}), 403)
    fmt = _ok(client.put("/exam-format", headers=a, json={"class_id": school.c1, "parts": PARTS}))
    assert [(p["name"], p["max_marks"]) for p in fmt["parts"]] == [
        ("Notebook", 5), ("Handwriting", 5), ("Term 1", 20), ("Final exam", 70)]
    assert fmt["total"] == 100 and fmt["other_exams"] == []
    exam = {p["name"]: p["exam_id"] for p in fmt["parts"]}
    listed = {e["name"]: e for e in _ok(client.get("/exams", headers=t1))}
    assert set(listed) == set(exam) and listed["Term 1"]["class_id"] == school.c1
    assert float(listed["Term 1"]["max_marks"]) == 20
    assert _ok(client.get(f"/exam-format?class_id={school.c1}", headers=t1))["total"] == 100
    twice = [*PARTS, {"name": "notebook", "max_marks": 5}]
    _ok(client.put("/exam-format", headers=a, json={"class_id": school.c1, "parts": twice}), 400)
    _ok(client.put("/exam-format", headers=a, json={"class_id": 999999, "parts": PARTS}), 404)

    # ---- Term 1 is held: marks entered on the exam itself and published (Maths was marked out of 40)
    def marks(subject, top, pairs):
        _ok(client.put(f"/exams/{exam['Term 1']}/results", headers=t1, json={
            "section_id": school.c1a, "subject": subject, "max_marks": top,
            "results": [{"student_id": s, "marks": m} for s, m in pairs]}))

    marks("English", 20, [(school.s1, 16), (school.s2, 10)])
    marks("Maths", 40, [(school.s1, 30), (school.s2, 20)])
    _ok(client.put(f"/exams/{exam['Term 1']}", headers=a, json={"results_published": True}))

    sheet = _ok(client.get(f"/final-results?section_id={school.c1a}", headers=t1))
    assert sheet["can_edit"] is True and sheet["published"] is False and sheet["format_total"] == 100
    assert [p["name"] for p in sheet["parts"]] == ["Notebook", "Handwriting", "Term 1", "Final exam"]
    one = sheet["students"][0]
    assert one["student_id"] == school.s1 and one["complete"] is False
    assert one["subjects"]["English"]["parts"][str(exam["Term 1"])] == 16
    assert one["subjects"]["Maths"]["parts"][str(exam["Term 1"])] == 15  # 30 of 40 counts as 15 of 20
    assert (one["total"], one["max_total"]) == (31, 40)
    assert _ok(client.get(f"/final-results?section_id={school.c1a}", headers=bearer(school.t2)))["can_edit"] is False
    _ok(client.get(f"/final-results?section_id={school.c1a}", headers=bearer(school.t3)), 403)

    # ---- the class teacher downloads the sheet: what is published is already in place
    _ok(client.get(f"/final-results/template?section_id={school.c1a}", headers=bearer(school.t2)), 403)
    r = client.get(f"/final-results/template?section_id={school.c1a}", headers=t1)
    assert r.status_code == 200, r.text
    ws = load_workbook(BytesIO(r.content))["Final result"]
    header = [c.value for c in ws[1]]
    assert header[:4] == ["student_id", "admission_no", "roll_no", "student_name"]
    assert header[4:8] == ["English | Notebook (5)", "English | Handwriting (5)", "English | Term 1 (20)",
                           "English | Final exam (70)"] and len(header) == 12
    row1 = [c.value for c in ws[2]]
    assert row1[0] == school.s1 and row1[header.index("English | Term 1 (20)")] == 16
    assert row1[header.index("Maths | Term 1 (20)")] == 15 and row1[header.index("English | Notebook (5)")] is None
    as_json = _ok(client.get(f"/final-results/template?section_id={school.c1a}&format=json", headers=t1))
    assert as_json["filename"] == "final-result-class-1-a.xlsx" and as_json["content_base64"]

    # ---- fills in the rest and uploads; a wrong cell saves nothing
    def upload(rows, who=t1):
        return client.post("/final-results/import", headers=who, data={"section_id": school.c1a},
                           files={"file": ("final.xlsx", _marks_file(header, rows), "application/octet-stream")})

    #        id, adm, roll, name, English: nb hw t1 final, Maths: nb hw t1 final
    full = [[school.s1, "", "", "", 4, 5, 16, 60, 5, 5, 15, 50],
            [school.s2, "", "", "", 3, 3, 10, "AB", 2, 2, 10, 20]]
    wrong = [list(full[0]), list(full[1])]
    wrong[0][7] = 71
    bad = upload(wrong)
    assert bad.status_code == 400 and bad.json()["code"] == "file_invalid"
    assert bad.json()["errors"] == [{"row": 2, "column": "English | Final exam (70)",
                                     "message": "71 — marks must be between 0 and 70"}]
    assert _ok(client.get(f"/final-results?section_id={school.c1a}", headers=t1))["students"][0]["total"] == 31
    _ok(upload(full, bearer(school.t2)), 403)
    done = _ok(upload(full))
    assert done["saved"] == {"students": 2, "marks": 12, "unchanged": 4}  # Term 1 was already there
    assert _ok(upload(full))["saved"] == {"students": 2, "marks": 0, "unchanged": 16}
    # Term 1's own marks were not rewritten (Maths is still 30 of 40 there)
    own = _ok(client.get(f"/exams/{exam['Term 1']}/results?section_id={school.c1a}&subject=Maths", headers=t1))
    assert (float(own["rows"][0]["marks"]), own["max_marks"]) == (30, 40)

    one, two = done["students"]
    assert one["subjects"]["English"] == {
        "parts": {str(exam["Notebook"]): 4, str(exam["Handwriting"]): 5, str(exam["Term 1"]): 16,
                  str(exam["Final exam"]): 60},
        "total": 85, "max_total": 100, "percentage": 85.0, "grade": "A", "complete": True}
    assert (one["subjects"]["Maths"]["total"], one["subjects"]["Maths"]["grade"]) == (75, "B")
    assert (one["total"], one["max_total"], one["percentage"], one["grade"], one["rank"]) == (160, 200, 80.0, "A", 1)
    assert two["subjects"]["English"]["parts"][str(exam["Final exam"])] == "AB"
    assert (two["subjects"]["English"]["total"], two["subjects"]["English"]["grade"]) == (16, "E")
    assert (two["total"], two["percentage"], two["grade"], two["rank"], two["complete"]) == (50, 25.0, "D", 2, True)
    assert done["incomplete"] == 0

    # ---- parents see it only once it is published
    kid = bearer(login(client, "student1", "studpass"))
    assert _ok(client.get("/student/final-result", headers=kid))["published"] is False
    _ok(client.put("/final-results/publish", headers=bearer(school.t2), json={"section_id": school.c1a}), 403)
    assert _ok(client.put("/final-results/publish", headers=t1, json={"section_id": school.c1a}))["published"] is True
    mine = _ok(client.get("/student/final-result", headers=kid))
    assert mine["published"] is True and (mine["total"], mine["max_total"], mine["grade"]) == (160, 200, "A")
    assert (mine["rank"], mine["class_size"], mine["class_name"], mine["section_name"]) == (1, 2, "Class 1", "A")
    english = mine["subjects"][0]
    assert english["subject"] == "English" and english["total"] == 85 and english["grade"] == "A"
    assert [(p["name"], p["marks"], p["max_marks"]) for p in english["parts"]] == [
        ("Notebook", 4, 5), ("Handwriting", 5, 5), ("Term 1", 16, 20), ("Final exam", 60, 70)]
    assert [b["grade"] for b in mine["grading"]] == ["A", "B", "C", "D", "E"]
    assert any(n["title"].endswith("Final result published") for n in _ok(client.get("/notifications", headers=kid)))
    assert _ok(client.get("/updates", headers=kid))["sections"]["exams"]["latest_at"]
    # another section of the class is not published with it
    assert _ok(client.get("/student/final-result", headers=bearer(login(client, "student3", "studpass"))))[
        "published"] is False
    # the parent's exam list and performance report use the school's grades too
    term = next(e for e in _ok(client.get("/student/exams", headers=kid)) if e["name"] == "Term 1")
    assert {s["subject"]: s["grade"] for s in term["subjects"]} == {"English": "A", "Maths": "B"}
    report = _ok(client.get("/student/performance", headers=kid))
    assert report["grades"][0] == {"min": 80.0, "grade": "A"} and report["overall"]["grade"] in "AB"

    # ---- the student's page: every exam + the final result, for the office and the student's teachers
    page = _ok(client.get(f"/students/{school.s1}/exam-results", headers=bearer(school.t2)))
    assert {e["name"] for e in page["exams"]} == set(exam)
    term = next(e for e in page["exams"] if e["name"] == "Term 1")
    assert [(s["subject"], s["marks"], s["max_marks"], s["grade"]) for s in term["subjects"]] == [
        ("English", 16, 20, "A"), ("Maths", 30, 40, "B")]
    assert (term["total"], term["max_total"], term["percentage"]) == (46, 60, 76.7)
    assert page["final"]["total"] == 160 and page["final"]["published"] is True
    _ok(client.get(f"/students/{school.s1}/exam-results", headers=bearer(school.t3)), 403)

    assert _ok(client.put("/final-results/publish", headers=a, json={"section_id": school.c1a, "published": False}))[
        "published"] is False
    assert _ok(client.get("/student/final-result", headers=kid))["published"] is False

    # ---- the same format for another class; a class that has one is left alone
    copied = _ok(client.post("/exam-format/copy", headers=a, json={"from_class_id": school.c1,
                                                                    "to_class_ids": [school.c2, school.c1]}))
    assert copied == {"copied": ["Class 2"], "skipped": []}
    again = _ok(client.post("/exam-format/copy", headers=a, json={"from_class_id": school.c1,
                                                                   "to_class_ids": [school.c2]}))
    assert again == {"copied": [], "skipped": ["Class 2"]}
    two_fmt = _ok(client.get(f"/exam-format?class_id={school.c2}", headers=a))
    assert [(p["name"], p["max_marks"]) for p in two_fmt["parts"]] == [(p["name"], p["max_marks"]) for p in PARTS]
    assert len(_ok(client.get("/exams", headers=a))) == 8

    # ---- taking an exam off the list: an empty one made here goes, one with marks stays in Exams
    keep = [{"exam_id": p["exam_id"], "name": p["name"], "max_marks": p["max_marks"]}
            for p in two_fmt["parts"] if p["name"] != "Handwriting"]
    assert len(_ok(client.put("/exam-format", headers=a, json={"class_id": school.c2, "parts": keep}))["parts"]) == 3
    assert len(_ok(client.get("/exams", headers=a))) == 7
    less = [{"exam_id": exam[n], "name": n, "max_marks": m} for n, m in (("Notebook", 10), ("Final exam", 90))]
    fmt = _ok(client.put("/exam-format", headers=a, json={"class_id": school.c1, "parts": less}))
    assert fmt["total"] == 100 and {o["name"] for o in fmt["other_exams"]} == {"Handwriting", "Term 1"}
    assert len(_ok(client.get("/exams", headers=a))) == 7  # both had marks: still there
    # an existing exam can be put (back) on the list
    back = [*less, {"exam_id": exam["Term 1"], "max_marks": 20}]
    assert [p["name"] for p in _ok(client.put("/exam-format", headers=a, json={
        "class_id": school.c1, "parts": back}))["parts"]] == ["Notebook", "Final exam", "Term 1"]
