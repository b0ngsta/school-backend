"""Nov 2026: full syllabus, remark edit/delete, exam results CSV (template +
upload by the class teacher) and the parent app's performance report."""
from __future__ import annotations

import base64
import io

import pytest
from helpers import PDF_BYTES, bearer, login
from openpyxl import Workbook, load_workbook

from app.services import performance
from app.services.spreadsheet import XLSX_MIME as XLSX


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


@pytest.fixture
def s1(client, school):
    return bearer(login(client, "student1", "studpass"))


def _exam(client, a, name, start, class_id=None, max_marks=100):
    body = {"name": name, "start_date": start, "end_date": start, "max_marks": max_marks}
    if class_id:
        body["class_id"] = class_id
    return _ok(client.post("/exams", headers=a, json=body), 201)["id"]


def _xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    out = io.BytesIO()
    wb.save(out)
    return {"file": ("results.xlsx", out.getvalue(), XLSX)}


def _read_xlsx(content):
    ws = load_workbook(io.BytesIO(content)).worksheets[0]
    return [[c for c in row] for row in ws.iter_rows(values_only=True)]


# ------------------------------------------------------------ full syllabus

def test_full_syllabus(client, school, s1):
    a, t2 = bearer(school.admin), bearer(school.t2)
    _ok(client.put(f"/classes/{school.c1}/subjects", headers=a, json={"subjects": ["Hindi", "Math"]}))
    assert _ok(client.get("/student/syllabus/full", headers=s1)) == []
    assert _ok(client.get("/student/dashboard", headers=s1))["full_syllabus"] == 0
    # a teacher of the class uploads the whole syllabus in one PDF (no subject)
    full = _ok(client.post("/documents", headers=t2, data={"kind": "syllabus", "title": "Syllabus 2026-27",
                                                           "class_id": school.c1},
                           files={"file": ("all.pdf", PDF_BYTES, "application/pdf")}), 201)
    assert full["subject"] is None
    _ok(client.post("/documents", headers=t2, data={"kind": "syllabus", "title": "Maths", "class_id": school.c1,
                                                    "subject": "Math"},
                    files={"file": ("m.pdf", PDF_BYTES, "application/pdf")}), 201)
    # still needs a class for teachers
    assert _ok(client.post("/documents", headers=t2, data={"kind": "syllabus", "title": "x"},
                           files={"file": ("m.pdf", PDF_BYTES, "application/pdf")}), 400)
    assert [d["title"] for d in _ok(client.get("/student/syllabus/full", headers=s1))] == ["Syllabus 2026-27"]
    assert _ok(client.get("/student/dashboard", headers=s1))["full_syllabus"] == 1
    groups = _ok(client.get("/student/syllabus", headers=s1))
    assert [(g["subject"], len(g["documents"])) for g in groups] == [("Hindi", 0), ("Math", 1)]
    # other class doesn't see it
    s4 = bearer(login(client, "student4", "studpass"))
    assert _ok(client.get("/student/syllabus/full", headers=s4)) == []
    # turning a subject syllabus into the full one and back
    changed = _ok(client.put(f"/documents/{full['id']}", headers=t2, data={"subject": "Hindi"}))
    assert changed["subject"] == "Hindi"
    assert _ok(client.get("/student/syllabus/full", headers=s1)) == []


# ------------------------------------------------------------ remarks

def test_remark_edit_and_delete(client, school):
    a, t1, t2 = bearer(school.admin), bearer(school.t1), bearer(school.t2)
    rid = _ok(client.post(f"/students/{school.s1}/remarks", headers=t2, json={"remark": "Good"}), 201)["id"]
    assert _ok(client.put(f"/students/{school.s1}/remarks/{rid}", headers=t2, json={"remark": " Very good "})) \
        == {"id": rid, "remark": "Very good"}
    assert _ok(client.get(f"/students/{school.s1}/remarks", headers=t1))[0]["remark"] == "Very good"
    # another teacher of the section can't change it; managers can
    assert _ok(client.put(f"/students/{school.s1}/remarks/{rid}", headers=t1, json={"remark": "x"}), 403)
    assert _ok(client.delete(f"/students/{school.s1}/remarks/{rid}", headers=t1), 403)
    assert _ok(client.put(f"/students/{school.s1}/remarks/{rid}", headers=t2, json={"remark": "  "}), 400)
    assert _ok(client.put(f"/students/{school.s2}/remarks/{rid}", headers=a, json={"remark": "x"}), 404)
    _ok(client.put(f"/students/{school.s1}/remarks/{rid}", headers=a, json={"remark": "Edited by office"}))
    _ok(client.delete(f"/students/{school.s1}/remarks/{rid}", headers=t2))
    assert _ok(client.get(f"/students/{school.s1}/remarks", headers=a)) == []
    assert _ok(client.delete(f"/students/{school.s1}/remarks/{rid}", headers=a), 404)


# ------------------------------------------------------------ results CSV

def test_results_excel_template_and_upload(client, school, s1):
    a, t1, t2 = bearer(school.admin), bearer(school.t1), bearer(school.t2)
    _ok(client.put(f"/classes/{school.c1}/subjects", headers=a, json={"subjects": ["English", "Math"]}))
    exam = _exam(client, a, "Term 1", "2026-09-01", school.c1, max_marks=None)
    url = f"/exams/{exam}/results"

    # only the class teacher (t1) or managers
    assert _ok(client.get(f"{url}/template?section_id={school.c1a}", headers=t2), 403)
    assert _ok(client.post(f"{url}/import", headers=t2, data={"section_id": school.c1a, "max_marks": 50},
                           files=_xlsx([["roll_no", "Math"], [1, 50]])), 403)
    assert _ok(client.get(f"{url}/sheet?section_id={school.c1a}", headers=t2))["can_import"] is False

    r = client.get(f"{url}/template?section_id={school.c1a}", headers=t1)
    assert r.status_code == 200 and r.headers["content-type"] == XLSX
    assert "attachment" in r.headers["content-disposition"] and ".xlsx" in r.headers["content-disposition"]
    rows = _read_xlsx(r.content)
    assert rows[0] == ["student_id", "admission_no", "roll_no", "student_name", "English", "Math", "remarks"]
    assert [row[2] for row in rows[1:]] == ["1", "2"]  # only section A, no MAX row any more
    assert load_workbook(io.BytesIO(r.content)).sheetnames == ["Marks", "How to fill"]
    as_json = _ok(client.get(f"{url}/template?section_id={school.c1a}&format=json", headers=t1))
    assert as_json["filename"].endswith(".xlsx") and as_json["mime"] == XLSX
    assert base64.b64decode(as_json["content_base64"])[:2] == b"PK"

    # the maximum is required and comes from the form, not the sheet
    rows[1][4:7] = [36, 45.5, "Well done"]
    rows[2][4:7] = [30.5, "AB", None]
    assert _ok(client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a},
                           files=_xlsx(rows)), 422)
    assert _ok(client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a, "max_marks": 0},
                           files=_xlsx(rows)), 422)
    # nothing published yet → nothing in the parent's performance report
    assert _ok(client.get("/student/performance", headers=s1))["overall"] is None
    done = _ok(client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a, "max_marks": 50},
                           files=_xlsx(rows)))
    assert (done["students"], done["marks"], done["absent"], done["remarks"], done["published"]) == (2, 4, 1, 1, True)

    sheet = _ok(client.get(f"{url}/sheet?section_id={school.c1a}", headers=t1))
    assert sheet["exam"]["results_published"] and sheet["exam"]["max_marks"] == 50.0  # exam had no maximum
    assert [s["max_marks"] for s in sheet["subjects"]] == [50.0, 50.0]
    first, second = sheet["students"]
    assert first["marks"] == {"English": 36.0, "Math": 45.5} and first["remarks"] == "Well done"
    assert (first["total"], first["max_total"], first["percentage"]) == (81.5, 100.0, 81.5)
    assert second["marks"] == {"English": 30.5, "Math": "AB"}
    eng = _ok(client.get(f"{url}?section_id={school.c1a}&subject=English", headers=t1))
    assert [row["marks"] for row in eng["rows"]] == [36.0, 30.5] and eng["max_marks"] == 50.0

    # ...and it shows straight away in the parent's Exams screen and Performance Report
    mine = next(e for e in _ok(client.get("/student/exams", headers=s1)) if e["id"] == exam)
    assert mine["results_published"] and mine["total"] == 81.5 and mine["max_total"] == 100.0
    rep = _ok(client.get("/student/performance", headers=s1))
    assert [e["name"] for e in rep["exams"]] == ["Term 1"] and rep["overall"]["percentage"] == 81.5

    # a re-download carries the saved marks; uploading it again changes nothing
    again = client.get(f"{url}/template?section_id={school.c1a}", headers=a).content
    rows2 = _read_xlsx(again)
    assert rows2[2][5] == "AB" and rows2[1][6] == "Well done" and rows2[1][4] == 36
    _ok(client.post(f"{url}/import", headers=a, data={"section_id": school.c1a, "max_marks": 50},
                    files={"file": ("r.xlsx", again, XLSX)}))
    assert _ok(client.get(f"{url}/sheet?section_id={school.c1a}", headers=t1))["students"] == sheet["students"]

    # all-or-nothing errors, with row numbers (marks above the maximum too)
    bad = client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a, "max_marks": 50},
                      files=_xlsx([["roll_no", "English", "Science"], [1, 10, 5]]))
    assert bad.status_code == 400 and bad.json()["code"] == "file_invalid"
    assert bad.json()["errors"][0]["column"] == "Science"
    bad = client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a, "max_marks": 50},
                      files=_xlsx([["roll_no", "English", "Math"], [1, 51, 50], [3, 1, 1],
                                   [2, "abc", None], [1, 1, 1]]))
    errs = bad.json()["errors"]
    assert bad.status_code == 400 and [(e["row"], e["column"]) for e in errs] == \
        [(2, "English"), (3, ""), (4, "English"), (5, "")]
    assert "between 0 and 50" in errs[0]["message"]
    assert _ok(client.get(f"{url}/sheet?section_id={school.c1a}", headers=t1))["students"][0][
        "marks"]["English"] == 36.0  # nothing changed
    # not a spreadsheet / old .xls
    junk = client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a, "max_marks": 50},
                       files={"file": ("r.xlsx", b"PK\x03\x04 broken", XLSX)})
    assert junk.status_code == 400 and "Excel" in junk.json()["detail"]
    old = client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a, "max_marks": 50},
                      files={"file": ("r.xls", b"\xd0\xcf\x11\xe0 old", "application/vnd.ms-excel")})
    assert old.status_code == 400 and ".xlsx" in old.json()["detail"]
    # a CSV (older app builds) still works; blanks are left alone
    _ok(client.post(f"{url}/import", headers=t1, data={"section_id": school.c1a, "max_marks": 50},
                    files={"file": ("r.csv", b"admission_no;math\nADM2;47\nADM1;\n", "text/csv")}))
    st = _ok(client.get(f"{url}/sheet?section_id={school.c1a}", headers=t1))["students"]
    assert (st[0]["marks"]["Math"], st[1]["marks"]["Math"]) == (45.5, 47.0)
    # another class's section
    assert _ok(client.get(f"{url}/sheet?section_id={school.c2a}", headers=a), 404)


# ------------------------------------------------------------ performance report

def test_grade_scale():
    assert [performance.grade_of(p) for p in (100, 91, 90.9, 81, 71, 61, 51, 41, 33, 32.9, None)] == \
        ["A1", "A1", "A2", "A2", "B1", "B2", "C1", "C2", "D", "E", None]


def test_performance_report(client, school, s1):
    a = bearer(school.admin)
    _ok(client.put(f"/classes/{school.c1}/subjects", headers=a, json={"subjects": ["English", "Math"]}))
    assert _ok(client.get("/student/performance", headers=s1))["overall"] is None
    ut1 = _exam(client, a, "UT-1", "2026-07-10", school.c1)
    half = _exam(client, a, "Half Yearly", "2026-09-20")  # all classes
    hidden = _exam(client, a, "Term 2", "2026-10-20", school.c1)

    def marks(exam, section, subject, values, max_marks=None):
        body = {"section_id": section, "subject": subject,
                "results": [{"student_id": sid, "marks": m} for sid, m in values]}
        if max_marks:
            body["max_marks"] = max_marks
        _ok(client.put(f"/exams/{exam}/results", headers=a, json=body))

    marks(ut1, school.c1a, "English", [(school.s1, 30), (school.s2, 40)], max_marks=50)
    marks(ut1, school.c1a, "Math", [(school.s1, 45), (school.s2, 20)], max_marks=50)
    marks(ut1, school.c1b, "English", [(school.s3, 50)], max_marks=50)
    marks(half, school.c1a, "English", [(school.s1, 70), (school.s2, 80)])
    marks(half, school.c1a, "Math", [(school.s1, 95), (school.s2, 60)])
    marks(hidden, school.c1a, "Math", [(school.s1, 10)])
    for e in (ut1, half):
        _ok(client.put(f"/exams/{e}", headers=a, json={"results_published": True}))

    rep = _ok(client.get("/student/performance", headers=s1))
    assert rep["session"]["label"] == "2026-2027"
    assert [e["name"] for e in rep["exams"]] == ["UT-1", "Half Yearly"]  # unpublished Term 2 left out
    ut, hy = rep["exams"]
    assert (ut["obtained"], ut["max_total"], ut["percentage"], ut["rank"], ut["class_size"]) == \
        (75.0, 100.0, 75.0, 2, 3)  # student 3 (other section, same class): English 50/50 = 100%
    ov = rep["overall"]
    assert (ov["obtained"], ov["max_total"], ov["percentage"], ov["grade"]) == (240.0, 300.0, 80.0, "B1")
    eng, math = rep["subjects"]
    assert (eng["subject"], eng["obtained"], eng["max_total"], eng["percentage"]) == ("English", 100.0, 150.0, 66.7)
    assert [h["percentage"] for h in eng["exams"]] == [60.0, 70.0] and eng["trend"] == 10.0
    assert (math["percentage"], math["rank"], math["class_size"]) == (93.3, 1, 2)
    kinds = [i["kind"] for i in rep["insights"]]
    assert kinds[:2] == ["strongest", "focus"] and rep["insights"][0]["subject"] == "Math"
    # other class: nothing of class 1
    s4 = bearer(login(client, "student4", "studpass"))
    assert _ok(client.get("/student/performance", headers=s4))["exams"] == []
    # the Performance Report dot lights up with published results
    assert _ok(client.get("/updates", headers=s1))["sections"]["report_cards"]["latest_at"] is not None
