"""Bulk school import from a folder of Excel files (HQ, 21 Nov 2026) and the class
teacher's "New password & share" (services/school_import/, services/login_share.py)."""
from __future__ import annotations

import io
import json
import zipfile

from helpers import bearer
from openpyxl import Workbook, load_workbook
from test_organisations import PLATFORM, _ok, login_to

CODE = "GVS"

CLASSES = [["Class name *", "Sections", "Subjects *"],
           ["5", "A, B", "English, Maths, EVS"],
           ["LKG", "", "English, Rhymes"]]
TEACHERS = [["Full name *", "Phone *", "Username", "Email", "Designation", "Main subject", "Qualification",
             "Date of joining", "Address"],
            ["Ravi Kumar", "+91 98765-43210", "", "", "TGT", "Maths", "M.Sc", "01-06-2021", ""],
            ["Seema Rao", "9123456780", "seema.r", "seema@example.com", "PRT", "English", "", "", ""]]
DRIVERS = [["Full name", "Phone", "Licence no."], ["Mohan Lal", "9812345678", "DL0420110012345"]]
STUDENT_HEAD = ["Full name *", "Admission no. *", "Roll no.", "Date of birth", "Father's name", "Mother's name",
                "Primary phone *", "Username", "Email", "Student's phone", "Address"]
CLASS5A = [STUDENT_HEAD,
           ["Aman Verma", "ADM1", "1", "2015-04-02", "Suresh Verma", "", "9000000001", "", "", "", "12 MG Road"],
           ["Anuj Verma", "ADM2", "2", "2016-07-09", "Suresh Verma", "", "9000000001", "", "", "", "12 MG Road"],
           ["Priya Shah", "ADM3", "3", "", "Rakesh Shah", "", "9000000002", "", "", "", ""]]
LKG = [STUDENT_HEAD, ["Tiny Tot", "ADM9", "1", "", "", "", "9000000009", "", "", "", ""]]


def xlsx(rows: list[list[str]]) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def folder(**files: list[list[str]]) -> dict[str, list[list[str]]]:
    base = {"classes.xlsx": CLASSES, "teachers.xlsx": TEACHERS, "drivers.xlsx": DRIVERS,
            "students/Class 5 - A.xlsx": CLASS5A, "students/LKG.xlsx": LKG}
    base.update({k.replace("__", "/").replace("_dash_", " - "): v for k, v in files.items()})
    return {k: v for k, v in base.items() if v is not None}


def upload(client, files: dict, dry_run: bool, code: str = CODE, extra: dict | None = None):
    names = [f"Green Valley/{name}" for name in files]
    parts = [("files", (name.rsplit("/", 1)[-1], xlsx(rows) if isinstance(rows, list) else rows,
                        "application/octet-stream")) for name, rows in files.items()]
    return client.post(f"/platform/organisations/{code}/bulk-import", headers=PLATFORM, files=parts,
                       data={"paths": json.dumps(names), "dry_run": "true" if dry_run else "false", **(extra or {})})


def new_school(client):
    _ok(client.post("/platform/organisations", headers=PLATFORM, json={
        "code": CODE, "name": "Green Valley School", "admin_password": "adminpass1"}), 201)
    admin = _ok(login_to(client, CODE, "admin", "adminpass1"))
    return admin


def test_a_whole_school_from_a_folder(client):
    admin = new_school(client)
    a = bearer(admin)

    check = _ok(upload(client, folder(), dry_run=True))
    assert check["ok"] is True and check["problems"] == []
    assert check["summary"] == {
        "classes_new": 2, "classes_updated": 0, "sections_new": 2, "subjects_new": 5,
        "teachers_new": 2, "teachers_updated": 0, "drivers_new": 1, "drivers_updated": 0,
        "students_new": 3, "students_updated": 0, "students_moved": 0, "sibling_checks": 1}
    labels = {f["path"]: f.get("label") for f in check["files"]}
    assert labels["Green Valley/students/Class 5 - A.xlsx"] == "5 — A"
    assert labels["Green Valley/students/LKG.xlsx"] == "LKG"
    assert [c["username"] for c in check["sibling_checks"]] == ["9000000001"]
    assert _ok(client.get("/classes", headers=a)) == []  # the check saved nothing

    done = _ok(upload(client, folder(), dry_run=False))
    assert done["saved"] is True
    logins = {x["full_name"]: x for x in done["logins"]}
    assert set(logins) == {"Ravi Kumar", "Seema Rao", "Mohan Lal"}
    assert logins["Ravi Kumar"]["username"] == "9876543210"  # the phone, as 10 digits
    assert logins["Seema Rao"]["username"] == "seema.r"
    pw = logins["Ravi Kumar"]["password"]
    assert pw.isalnum() and pw.islower() and pw[-4:].isdigit()
    teacher = _ok(login_to(client, CODE, "9876543210", pw))
    assert teacher["must_change_password"] is True and teacher["user_type"] == "teacher"
    assert _ok(login_to(client, CODE, "9812345678", logins["Mohan Lal"]["password"]))["user_type"] == "driver"

    classes = {c["name"]: c for c in _ok(client.get("/classes", headers=a))}
    assert set(classes) == {"5", "LKG"}
    assert [s["name"] for s in classes["5"]["sections"]] == ["A", "B"] and classes["5"]["fee_amount"] is None
    assert classes["LKG"]["has_sections"] == 0
    assert classes["5"]["subjects"] == ["English", "Maths", "EVS"]

    sec5a = next(s["id"] for s in classes["5"]["sections"] if s["name"] == "A")
    students = _ok(client.get(f"/students?class_id={classes['5']['id']}&section_id={sec5a}", headers=a))
    assert [(s["full_name"], s["username"], s["roll_no"]) for s in students] == [
        ("Aman Verma", "9000000001", "1"), ("Priya Shah", "9000000002", "3")]
    assert students[0]["address"] == "12 MG Road" and students[0]["dob"] == "2015-04-02"
    # a student has no password until the class teacher shares one
    assert login_to(client, CODE, "9000000002", "anything1").status_code == 401

    # Anuj shares Aman's number: not added yet, HQ asks "brother / sister?"
    [anuj] = done["sibling_checks"]
    assert anuj["full_name"] == "Anuj Verma" and anuj["class_label"] == "5 — A"
    assert [m["full_name"] for m in anuj["matches"]] == ["Aman Verma"]
    added = _ok(client.post(f"/platform/organisations/{CODE}/bulk-import/siblings", headers=PLATFORM,
                            json={"student": anuj, "sibling": True}), 201)
    assert added["username"] == "9000000001" and [s["full_name"] for s in added["siblings"]] == ["Aman Verma"]
    again = client.post(f"/platform/organisations/{CODE}/bulk-import/siblings", headers=PLATFORM,
                        json={"student": anuj, "sibling": True})
    assert again.status_code == 409  # admission no. already in the app

    lic = client.get(f"/platform/organisations/{CODE}/users?role=driver", headers=PLATFORM).json()
    assert lic[0]["username"] == "9812345678"


def test_a_check_says_no_and_a_new_username_works(client):
    new_school(client)
    _ok(upload(client, folder(), dry_run=False))
    priya_twin = {"full_name": "Riya Shah", "admission_no": "ADM4", "guardian_phone": "9000000002",
                  "class_name": "5", "section_name": "B", "username": "9000000002"}
    r = client.post(f"/platform/organisations/{CODE}/bulk-import/siblings", headers=PLATFORM,
                    json={"student": priya_twin, "sibling": False, "username": "9000000002"})
    assert r.status_code == 409 and r.json()["code"] == "username_shared"
    added = _ok(client.post(f"/platform/organisations/{CODE}/bulk-import/siblings", headers=PLATFORM,
                            json={"student": priya_twin, "sibling": False, "username": "riya.shah"}), 201)
    assert added["username"] == "riya.shah" and added["class_label"] == "5 — B" and added["siblings"] == []


def test_any_problem_stops_the_whole_upload(client):
    admin = new_school(client)
    bad_phone = [STUDENT_HEAD, ["Bad Phone", "ADM5", "", "", "", "", "12345", "", "", "", ""]]
    files = folder(**{"students__Class 5_dash_B.xlsx": bad_phone, "students__Class 9_dash_Z.xlsx": LKG})
    files["notes.txt"] = b"hello"
    files["teachers.xlsx"] = TEACHERS + [["No Phone", "", "", "", "", "", "", "", ""]]

    check = _ok(upload(client, files, dry_run=True))
    assert check["ok"] is False
    found = {(p["file"].rsplit("/", 1)[-1], p["row"], p["column"]) for p in check["problems"]}
    assert ("Class 5 - B.xlsx", 2, "Primary phone") in found
    assert ("teachers.xlsx", 4, "Phone") in found
    assert any(p["file"].endswith("Class 9 - Z.xlsx") and "doesn't match a class" in p["message"]
               for p in check["problems"])
    assert check["ignored"] == [{"path": "Green Valley/notes.txt", "reason": "not an Excel (.xlsx) file"}]

    r = upload(client, files, dry_run=False)
    assert r.status_code == 400 and r.json()["code"] == "import_invalid"
    assert r.json()["report"]["problems"] == check["problems"]
    assert _ok(client.get("/classes", headers=bearer(admin))) == []  # nothing at all was saved
    assert client.get(f"/platform/organisations/{CODE}/users?role=teacher", headers=PLATFORM).json() == []

    # usernames: a student can't take a staff login; a staff phone already in the app needs its username
    clash = [STUDENT_HEAD, ["Teacher's Kid", "ADM7", "", "", "", "", "9876543210", "", "", "", ""]]
    check = _ok(upload(client, folder(**{"students__Class 5_dash_B.xlsx": clash}), dry_run=True))
    assert [(p["column"], "staff" in p["message"] or "teacher" in p["message"]) for p in check["problems"]] == [
        ("Primary phone", True)]
    no_classes = folder()
    no_classes.pop("classes.xlsx")
    missing = _ok(upload(client, no_classes, dry_run=True))
    assert missing["ok"] is False  # no classes.xlsx and no classes in the app: the student files match nothing


def test_uploading_again_updates_and_removes_nothing(client):
    admin = new_school(client)
    a = bearer(admin)
    _ok(upload(client, folder(), dry_run=False))

    classes = [["Class name", "Sections", "Subjects"], ["5", "A, C", "Maths, Science"], ["LKG", "A, B", "Art"]]
    teachers = [TEACHERS[0], ["Ravi K. Kumar", "9876543210", "", "", "PGT", "", "", "", ""]]
    moved = [STUDENT_HEAD, ["Priya S. Shah", "ADM3", "7", "", "", "", "9000000002", "", "", "", ""]]
    files = {"classes.xlsx": classes, "teachers.xlsx": teachers, "students/Class 5 - C.xlsx": moved}
    check = _ok(upload(client, files, dry_run=True))
    assert check["ok"], check["problems"]
    s = check["summary"]
    assert (s["classes_new"], s["classes_updated"], s["sections_new"], s["subjects_new"]) == (0, 2, 3, 2)
    assert (s["teachers_updated"], s["students_updated"], s["students_moved"], s["students_new"]) == (1, 1, 1, 0)
    _ok(upload(client, files, dry_run=False))

    cls = {c["name"]: c for c in _ok(client.get("/classes", headers=a))}
    assert [x["name"] for x in cls["5"]["sections"]] == ["A", "B", "C"]          # B kept, C added
    assert cls["5"]["subjects"] == ["English", "Maths", "EVS", "Science"]         # nothing removed
    assert cls["LKG"]["has_sections"] == 1
    lkg = [x["name"] for x in cls["LKG"]["sections"]]
    assert sorted(lkg) == ["A", "B"]  # the class's one group became section A (its student stays in it)
    c = next(x for x in cls["5"]["sections"] if x["name"] == "C")
    [priya] = _ok(client.get(f"/students?class_id={cls['5']['id']}&section_id={c['id']}", headers=a))
    assert (priya["full_name"], priya["roll_no"], priya["father_name"]) == ("Priya S. Shah", "7", "Rakesh Shah")
    t = _ok(client.get("/users/teachers", headers=a))
    assert {x["full_name"]: x["designation"] for x in t} == {"Ravi K. Kumar": "PGT", "Seema Rao": "PRT"}


def test_templates_round_trip(client):
    empty = client.get("/platform/bulk-import/template", headers=PLATFORM)
    assert empty.status_code == 200 and empty.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(empty.content)).namelist()
    assert {"School/classes.xlsx", "School/teachers.xlsx", "School/drivers.xlsx",
            "School/students/Class 1 - A.xlsx", "School/README.txt"} <= set(names)
    head = next(load_workbook(io.BytesIO(zipfile.ZipFile(io.BytesIO(empty.content)).read(
        "School/students/Class 1 - A.xlsx"))).active.iter_rows(values_only=True))
    assert head[:3] == ("Full name *", "Admission no. *", "Roll no.")

    new_school(client)
    _ok(upload(client, folder(), dry_run=False))
    filled = _ok(client.get(f"/platform/bulk-import/template?school={CODE}&format=json", headers=PLATFORM))
    import base64
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(filled["content_base64"])))
    files = {n.split("/", 1)[1]: z.read(n) for n in z.namelist() if n.endswith(".xlsx")}
    assert "students/Class 5 - A.xlsx" in files and "students/LKG.xlsx" in files
    drivers = list(load_workbook(io.BytesIO(files["drivers.xlsx"])).active.iter_rows(values_only=True))
    assert drivers[1] == ("Mohan Lal", "9812345678", "9812345678", None, "DL0420110012345")
    back = _ok(upload(client, files, dry_run=True))
    assert back["ok"], back["problems"]
    s = back["summary"]
    assert (s["classes_new"], s["teachers_new"], s["drivers_new"], s["students_new"]) == (0, 0, 0, 0)
    assert (s["teachers_updated"], s["drivers_updated"], s["students_updated"]) == (2, 1, 3)


def test_bulk_import_needs_the_platform_secret(client):
    for method, path in (("GET", "/platform/bulk-import/template"),
                         ("POST", "/platform/organisations/DEFAULT/bulk-import"),
                         ("POST", "/platform/organisations/DEFAULT/bulk-import/siblings")):
        assert client.request(method, path, headers={"X-Platform-Secret": "wrong"}).status_code == 403
    assert client.post("/platform/organisations/NOPE/bulk-import", headers=PLATFORM,
                       files=[("files", ("classes.xlsx", xlsx(CLASSES)))]).status_code == 404


def test_class_teacher_shares_a_login(client):
    admin = new_school(client)
    a = bearer(admin)
    done = _ok(upload(client, folder(), dry_run=False))
    ravi = next(x for x in done["logins"] if x["full_name"] == "Ravi Kumar")
    seema = next(x for x in done["logins"] if x["full_name"] == "Seema Rao")
    cls = {c["name"]: c for c in _ok(client.get("/classes", headers=a))}
    sec5a = next(s["id"] for s in cls["5"]["sections"] if s["name"] == "A")
    sec5b = next(s["id"] for s in cls["5"]["sections"] if s["name"] == "B")
    _ok(client.post("/assignments", headers=a, json={"teacher_id": ravi["id"], "class_id": cls["5"]["id"],
                                                     "section_id": sec5a, "role": "class_teacher"}), 201)
    _ok(client.post("/assignments", headers=a, json={"teacher_id": seema["id"], "class_id": cls["5"]["id"],
                                                     "section_id": sec5a, "role": "subject_teacher",
                                                     "subject": "English"}), 201)
    t = bearer(_ok(login_to(client, CODE, ravi["username"], ravi["password"])))
    sub = bearer(_ok(login_to(client, CODE, seema["username"], seema["password"])))

    status = _ok(client.get(f"/students/login-status?section_id={sec5a}", headers=t))
    assert status["class_label"] == "5 — A"
    assert [(s["full_name"], s["signed_in"], s["last_shared_at"]) for s in status["students"]] == [
        ("Aman Verma", False, None), ("Priya Shah", False, None)]
    aman = status["students"][0]["id"]

    assert client.get(f"/students/login-status?section_id={sec5a}", headers=sub).status_code == 403
    assert client.post(f"/students/{aman}/login-share", headers=sub).status_code == 403
    assert client.get(f"/students/login-status?section_id={sec5b}", headers=t).status_code == 403

    shared = _ok(client.post(f"/students/{aman}/login-share", headers=t))
    assert shared["username"] == "9000000001" and shared["school_code"] == CODE and shared["was_signed_in"] is False
    assert shared["password"] in shared["share_text"] and "Green Valley School" in shared["share_text"]
    parent = _ok(login_to(client, CODE, "9000000001", shared["password"]))
    assert parent["must_change_password"] is True and parent["full_name"] == "Aman Verma"

    again = _ok(client.post(f"/students/{aman}/login-share", headers=bearer(admin)))  # managers too
    assert again["was_signed_in"] is True  # the parent's phone is signed out by the new password
    assert client.post("/auth/refresh", json={"refresh_token": parent["refresh_token"]}).status_code == 401
    assert login_to(client, CODE, "9000000001", shared["password"]).status_code == 401
    after = _ok(client.get(f"/students/login-status?section_id={sec5a}", headers=t))
    assert after["students"][0]["last_shared_at"] is not None

    _ok(client.delete(f"/students/{aman}?reason=moved", headers=a))
    assert client.post(f"/students/{aman}/login-share", headers=a).status_code == 409
