"""Bulk user import from CSV (+ photos ZIP), template and export."""
from __future__ import annotations

import csv
import dataclasses
import io
import zipfile

import pytest
from helpers import bearer, jpeg_with_gps, login, png_bytes
from PIL import Image

from app.core.config import settings
from app.core.http import BodySizeLimitMiddleware
from app.db import Db, pool
from app.services.user_import import service as import_service
from app.services.user_import import writer as import_writer

STUDENT_HEADER = ["username", "password", "full_name", "email", "phone", "admission_no", "roll_no", "dob",
                  "father_name", "guardian_phone", "photo"]


def make_csv(rows: list[list[str]], header: list[str] = STUDENT_HEADER, delimiter: str = ",",
             bom: bool = False) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(header)
    writer.writerows(rows)
    return (("﻿" if bom else "") + out.getvalue()).encode()


def make_zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def upload(client, who, user_type: str, data: bytes, *, section: tuple[int, int] | None = None,
           save: bool = False, photos: bytes | None = None):
    form = {"user_type": user_type, "dry_run": "false" if save else "true"}
    if section:
        form["class_id"], form["section_id"] = str(section[0]), str(section[1])
    files = {"file": ("users.csv", data, "text/csv")}
    if photos is not None:
        files["photos"] = ("photos.zip", photos, "application/zip")
    return client.post("/imports/users", data=form, files=files, headers=bearer(who))


def kid(n: int, **over) -> list[str]:
    row = {"username": f"kid{n}", "password": f"kidpass{n}", "full_name": f"Kid {n}",
           "email": f"parent{n}@example.com", "phone": "", "admission_no": f"NEW{n}", "roll_no": str(n),
           "dob": "2015-04-01", "father_name": "", "guardian_phone": "", "photo": ""}
    row.update(over)
    return [row[h] for h in STUDENT_HEADER]


def rows_by_user(report: dict) -> dict[str, dict]:
    return {r["username"]: r for r in report["rows"]}


# ------------------------------------------------------------ the basics

def test_columns_and_template(client, school):
    r = client.get("/imports/users/columns", params={"user_type": "student"}, headers=bearer(school.admin))
    assert r.status_code == 200
    spec = r.json()
    assert spec["needs_section"] is True and spec["match_on"] == "username"
    names = [c["name"] for c in spec["columns"]]
    assert names[:5] == ["username", "password", "full_name", "email", "phone"] and "admission_no" in names
    assert {c["name"] for c in spec["columns"] if c["required"]} == {"username", "password", "full_name", "email"}

    t = client.get("/imports/users/template", params={"user_type": "teacher"}, headers=bearer(school.admin))
    assert t.headers["content-type"].startswith("text/csv")
    assert "attachment" in t.headers["content-disposition"]
    assert t.content.decode("utf-8-sig").strip() == ("username,password,full_name,email,phone,subject,"
                                                     "qualification,joining_date,address,photo")


def test_preview_then_save_students(client, school, db):
    data = make_csv([kid(10, phone="+91 98765 43210"), kid(11, dob="21/05/2014"), kid(12, roll_no="")])
    preview = upload(client, school.admin, "student", data, section=(school.c1, school.c1a))
    assert preview.status_code == 200, preview.text
    report = preview.json()
    assert report["dry_run"] is True and report["saved"] is False
    assert report["summary"] == {"rows": 3, "create": 3, "update": 0, "unchanged": 0, "error": 0, "warnings": 0}
    assert (report["class_name"], report["section_name"]) == ("Class 1", "A")
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username LIKE 'kid%%'") == 0  # preview saves nothing

    saved = upload(client, school.admin, "student", data, section=(school.c1, school.c1a), save=True)
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] is True
    created = rows_by_user(saved.json())
    assert all(r["action"] == "create" and r["user_id"] for r in created.values())

    row = db.one("SELECT u.email, u.phone, u.must_change_password, sp.class_id, sp.section_id, sp.roll_no, "
                 "sp.admission_no, sp.dob FROM users u JOIN student_profiles sp ON sp.user_id = u.id "
                 "WHERE u.username = 'kid11'")
    assert row["email"] == "parent11@example.com" and row["must_change_password"] == 1
    assert (row["class_id"], row["section_id"], row["roll_no"], row["admission_no"]) == \
        (school.c1, school.c1a, "11", "NEW11")
    assert row["dob"].isoformat() == "2014-05-21"
    assert db.scalar("SELECT roll_no FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
                     "WHERE u.username = 'kid12'") is None

    tokens = login(client, "kid10", "kidpass10")
    assert tokens["user_type"] == "student" and tokens["must_change_password"] is True
    listed = client.get("/students", params={"class_id": school.c1, "section_id": school.c1a},
                        headers=bearer(school.t1)).json()
    assert {"kid10", "kid11", "kid12"} <= {s["username"] for s in listed}

    audit = db.one("SELECT details FROM audit_logs WHERE action = 'users.import'")
    assert '"created"' in audit["details"] and "kidpass" not in audit["details"]


def test_reupload_updates_only_what_changed(client, school, db):
    a = (school.c1, school.c1a)
    upload(client, school.admin, "student", make_csv([kid(10), kid(11)]), section=a, save=True)

    exported = client.get("/imports/users/export", params={"user_type": "student", "class_id": school.c1,
                                                           "section_id": school.c1a}, headers=bearer(school.admin))
    assert exported.status_code == 200
    table = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert [r["username"] for r in table] == ["student1", "student2", "kid10", "kid11"]
    assert all(r["password"] == "" and r["photo"] == "" for r in table)

    for r in table:
        r["email"] = r["email"] or f"{r['username']}@example.com"   # seeded students have no email yet
    table[2]["phone"] = "9000000010"
    table[3]["father_name"] = ""          # blank keeps the current value
    table[3]["password"] = "ignored-pw"   # existing accounts keep their password
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(table[0]))
    w.writeheader()
    w.writerows(table)

    report = upload(client, school.t1, "student", out.getvalue().encode(), section=a, save=True)
    assert report.status_code == 200, report.text
    by_user = rows_by_user(report.json())
    assert by_user["kid10"]["action"] == "update" and by_user["kid10"]["changes"] == ["phone"]
    assert by_user["student1"]["changes"] == ["email"]
    assert by_user["kid11"]["action"] == "unchanged" and by_user["kid11"]["warnings"] == []
    assert report.json()["warnings"] == ["Passwords were ignored for 1 existing user: existing accounts keep "
                                         "their password (they can use 'Forgot password')."]
    assert login(client, "kid11", "kidpass11")  # password unchanged
    assert db.scalar("SELECT phone FROM users WHERE username = 'kid10'") == "9000000010"
    assert db.scalar("SELECT father_name FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
                     "WHERE u.username = 'student2'") == "Father 2"

    again = upload(client, school.t1, "student", out.getvalue().encode(), section=a)
    assert again.json()["summary"]["unchanged"] == 4


def test_who_may_import_what(client, school):
    data = make_csv([kid(20)])
    assert upload(client, school.t1, "student", data, section=(school.c1, school.c1a)).status_code == 200
    # class teacher of another section, subject teacher, non-student types, drivers
    assert upload(client, school.t1, "student", data, section=(school.c1, school.c1b)).status_code == 403
    assert upload(client, school.t2, "student", data, section=(school.c1, school.c1a)).status_code == 403
    staff = make_csv([["newt", "secret1", "New T", "t@example.com"]], header=["username", "password", "full_name",
                                                                              "email"])
    r = upload(client, school.t1, "teacher", staff)
    assert r.status_code == 403 and "Teachers can only add students" in r.json()["detail"]
    driver = login(client, "driver")
    assert upload(client, driver, "student", data, section=(school.c1, school.c1a)).status_code == 403
    coordinator = login(client, "coordinator")
    assert upload(client, coordinator, "teacher", staff).status_code == 200
    r = upload(client, coordinator, "sub_admin", staff)
    assert r.status_code == 403 and "Only admin" in r.json()["detail"]
    assert upload(client, school.admin, "sub_admin", staff).status_code == 200
    # students need a section, which must exist in that class
    assert upload(client, school.admin, "student", data).status_code == 400
    assert upload(client, school.admin, "student", data, section=(school.c2, school.c1a)).status_code == 404
    # the template and the export follow the same rules
    assert client.get("/imports/users/template", params={"user_type": "teacher"},
                      headers=bearer(school.t1)).status_code == 403
    assert client.get("/imports/users/export", params={"user_type": "student", "class_id": school.c1,
                                                       "section_id": school.c1b},
                      headers=bearer(school.t1)).status_code == 403


def test_students_enrolled_elsewhere_are_reported_with_their_class(client, school):
    data = make_csv([
        kid(30, username="student3", full_name="Student 3", admission_no="ADM3", roll_no="3"),  # in 1-B
        kid(31, admission_no="ADM4"),                                                         # ADM4 is in 2-A
        kid(32, roll_no="1"),                                                                 # roll 1 taken in 1-A
    ])
    report = upload(client, school.t1, "student", data, section=(school.c1, school.c1a)).json()
    by_user = rows_by_user(report)
    assert by_user["student3"]["errors"] == [
        "Student 3 is already enrolled in Class 1-B. Contact that class teacher (or the office) to move the "
        "student, or remove this row."]
    assert by_user["kid31"]["errors"] == ["Admission number ADM4 already belongs to Student 4 (in Class 2-A)."]
    assert by_user["kid32"]["errors"] == ["Roll number 1 already belongs to Student 1 (in this section)."]
    assert report["summary"]["error"] == 3


def test_bad_rows_are_skipped_and_the_rest_saved(client, school, db):
    sec = (school.c1, school.c1a)
    rows = [kid(40), kid(41, email="not-an-email"), kid(42)]
    r = upload(client, school.admin, "student", make_csv(rows), section=sec, save=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] is True
    assert body["summary"] == {"rows": 3, "create": 2, "update": 0, "unchanged": 0, "error": 1, "warnings": 0}
    by_user = rows_by_user(body)
    assert by_user["kid41"]["action"] == "error" and by_user["kid41"]["user_id"] is None
    assert by_user["kid40"]["user_id"] and by_user["kid42"]["user_id"]
    saved = {u["username"] for u in db.all("SELECT username FROM users WHERE username LIKE 'kid4%%'")}
    assert saved == {"kid40", "kid42"}
    audit = db.one("SELECT details FROM audit_logs WHERE action = 'users.import'")["details"]
    assert '"skipped_rows": [3]' in audit

    # fix the bad row and upload the same file again: the saved rows show as unchanged
    rows[1] = kid(41)
    again = upload(client, school.admin, "student", make_csv(rows), section=sec, save=True).json()
    assert [r["action"] for r in again["rows"]] == ["unchanged", "create", "unchanged"]
    assert login(client, "kid41", "kidpass41")


def test_nothing_to_save_is_a_422(client, school, db):
    data = make_csv([kid(43, email="not-an-email"), kid(44, password="")])
    r = upload(client, school.admin, "student", data, section=(school.c1, school.c1a), save=True)
    assert r.status_code == 422
    body = r.json()
    assert body["detail"].startswith("Nothing was saved: 2 rows have problems")
    assert body["saved"] is False and body["summary"]["error"] == 2


def test_skipping_a_row_keeps_its_numbers(client, school, db):
    """student1 wants to give roll 1 to a new student, but student1's row is
    skipped, so student1 keeps roll 1 and the new row cannot have it."""
    header = ["username", "password", "full_name", "email", "roll_no"]
    sec = (school.c1, school.c1a)
    data = make_csv([["student1", "", "Student 1", "bad-email", "9"],
                     ["kid45", "kidpass45", "Kid 45", "k45@example.com", "1"]], header=header)
    rows = upload(client, school.t1, "student", data, section=sec, save=True).json()["rows"]
    assert rows[0]["errors"] == ["email: 'bad-email' — enter a valid email address."]
    assert rows[1]["errors"] == ["Roll number 1 already belongs to Student 1 (in this section)."]
    # with student1's row fixed, both are saved in one go
    data = make_csv([["student1", "", "Student 1", "s1@example.com", "9"],
                     ["kid45", "kidpass45", "Kid 45", "k45@example.com", "1"]], header=header)
    r = upload(client, school.t1, "student", data, section=sec, save=True)
    assert r.status_code == 200 and r.json()["summary"]["error"] == 0


def test_row_checks(client, school):
    data = make_csv([
        kid(50),
        kid(51, username="KID50"),                         # same username, other case
        kid(52, password=""),                              # new user without password
        kid(53, dob="31-02-2015"),                         # impossible date
        kid(54, phone="9.87654E+09"),                      # Excel mangled the number
        kid(55, full_name="x" * 101),                      # too long
        kid(56, username="two words"),
        kid(57, password="123"),
        kid(58, username="kid58", email=""),
        kid(59, admission_no="NEW50"),                     # admission number repeated in the file
    ])
    by_row = {r["row"]: r for r in upload(client, school.admin, "student", data,
                                          section=(school.c1, school.c1a)).json()["rows"]}
    # repeated values make every row that has them ambiguous
    assert by_row[2]["errors"] == ["Username 'kid50' is on rows 2 and 3; keep it on one row only.",
                                   "Admission number NEW50 is on rows 2 and 11; keep it on one row only."]
    assert by_row[3]["errors"] == ["Username 'KID50' is on rows 2 and 3; keep it on one row only."]
    assert by_row[4]["errors"] == ["New user: password is required."]
    assert "not a date" in by_row[5]["errors"][0]
    assert "changed by Excel" in by_row[6]["errors"][0]
    assert by_row[7]["errors"] == ["full_name is too long (101 characters, max 100)."]
    assert by_row[8]["errors"] == ["Usernames cannot contain spaces."]
    assert by_row[9]["errors"] == ["password must be at least 6 characters."]
    assert by_row[10]["errors"] == ["New user: email is required."]
    assert by_row[11]["errors"] == ["Admission number NEW50 is on rows 2 and 11; keep it on one row only."]


def test_roll_numbers_can_be_swapped_in_one_file(client, school, db):
    header = ["username", "full_name", "email", "roll_no"]
    data = make_csv([["student1", "Student 1", "s1@example.com", "2"],
                     ["student2", "Student 2", "s2@example.com", "1"]], header=header)
    r = upload(client, school.t1, "student", data, section=(school.c1, school.c1a), save=True)
    assert r.status_code == 200, r.text
    rolls = {row["username"]: row["roll_no"] for row in db.all(
        "SELECT u.username, sp.roll_no FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE u.username IN ('student1', 'student2')")}
    assert rolls == {"student1": "2", "student2": "1"}
    # a new student may take a number another student in the file gives up
    data = make_csv([["student1", "", "Student 1", "s1@example.com", "7"],
                     ["kid60", "kidpass60", "Kid 60", "k@example.com", "2"]],
                    header=["username", "password", "full_name", "email", "roll_no"])
    r = upload(client, school.t1, "student", data, section=(school.c1, school.c1a), save=True)
    assert r.status_code == 200, r.text


def test_skipped_rows_cascade(client, school):
    """student2 cannot be saved (its admission number belongs to Student 4),
    so it keeps roll 2; then student1 cannot move to roll 2 and keeps roll 1;
    then the new student cannot take roll 1."""
    header = ["username", "password", "full_name", "email", "admission_no", "roll_no"]
    data = make_csv([["student1", "", "Student 1", "s1@example.com", "", "2"],
                     ["student2", "", "Student 2", "s2@example.com", "ADM4", "3"],
                     ["kid46", "kidpass46", "Kid 46", "k46@example.com", "", "1"]], header=header)
    rows = upload(client, school.admin, "student", data, section=(school.c1, school.c1a)).json()["rows"]
    assert [r["errors"] for r in rows] == [
        ["Roll number 2 already belongs to Student 2 (in this section)."],
        ["Admission number ADM4 already belongs to Student 4 (in Class 2-A)."],
        ["Roll number 1 already belongs to Student 1 (in this section)."],
    ]


def test_one_principal(client, school, db):
    header = ["username", "password", "full_name", "email"]
    second = make_csv([["vice", "secret12", "Vice Principal", "vp@example.com"]], header=header)
    r = upload(client, school.admin, "principal", second).json()
    assert r["rows"][0]["errors"] == ["There is already a principal (principal). Only one principal is allowed."]
    # the existing principal can be updated by CSV
    same = make_csv([["principal", "", "Principal", "head@example.com"]], header=header)
    assert upload(client, school.admin, "principal", same, save=True).status_code == 200
    assert db.scalar("SELECT email FROM users WHERE username = 'principal'") == "head@example.com"
    # with no principal, a file may add exactly one
    db.execute("DELETE FROM users WHERE username = 'principal'")
    two = make_csv([["head1", "secret12", "Head One", "h1@example.com"],
                    ["head2", "secret12", "Head Two", "h2@example.com"]], header=header)
    rows = upload(client, school.admin, "principal", two).json()["rows"]
    message = "Only one principal is allowed, but rows 2 and 3 each add one; keep one."
    assert rows[0]["errors"] == rows[1]["errors"] == [message]
    assert upload(client, school.admin, "principal", two, save=True).status_code == 422
    one = make_csv([["head1", "secret12", "Head One", "h1@example.com"]], header=header)
    assert upload(client, school.admin, "principal", one, save=True).status_code == 200
    assert upload(client, login(client, "subadmin"), "principal", second).status_code == 403


def test_teachers_with_profile_fields(client, school, db):
    header = ["Username", "Password", "Name", "E-mail", "Subject", "Qualification", "Date of Joining", "Blood group"]
    data = make_csv([["mrs.rao", "secret12", "Lakshmi Rao", "rao@example.com", "Physics", "M.Sc", "01-06-2021", "O+"],
                     ["mr.das", "secret12", "Arun Das", "das@example.com", "", "", "", ""]], header=header)
    r = upload(client, school.admin, "teacher", data, save=True)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == ["Column 'Blood group' is not used for teacher accounts and was ignored."]
    teachers = {t["username"]: t for t in client.get("/users/teachers", headers=bearer(school.admin)).json()}
    assert teachers["mrs.rao"]["subject"] == "Physics" and teachers["mrs.rao"]["joining_date"] == "2021-06-01"
    assert db.scalar("SELECT COUNT(*) FROM teacher_profiles tp JOIN users u ON u.id = tp.user_id "
                     "WHERE u.username = 'mr.das'") == 1

    # older teachers may have no profile row: the update creates it
    db.execute("DELETE FROM teacher_profiles WHERE user_id = %s", (school.t1_id,))
    update = make_csv([["teacher1", "", "Teacher 1", "t1@example.com", "", "B.Ed", "", ""]], header=header)
    r = upload(client, school.admin, "teacher", update, save=True)
    assert rows_by_user(r.json())["teacher1"]["changes"] == ["email", "qualification"]
    assert db.scalar("SELECT qualification FROM teacher_profiles WHERE user_id = %s", (school.t1_id,)) == "B.Ed"

    # a username that belongs to another kind of account is refused
    clash = make_csv([["driver", "", "Driver", "d@example.com", "", "", "", ""]], header=header)
    assert upload(client, school.admin, "teacher", clash).json()["rows"][0]["errors"] == [
        "Username 'driver' is already used by a driver account."]


# ---------------------------------------------------------------- photos

def test_photos_from_a_zip(client, school, db):
    zipped = make_zip({"Class1A/KID70.JPG": jpeg_with_gps(), "kid71.png": png_bytes(), "spare.jpg": png_bytes(),
                       "__MACOSX/._KID70.JPG": b"junk"})
    data = make_csv([kid(70, photo="kid70.jpg"), kid(71, photo="kid71.png")])
    r = upload(client, school.admin, "student", data, section=(school.c1, school.c1a), save=True, photos=zipped)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == ["1 photo(s) in the ZIP are not used by any row: spare.jpg."]

    path = db.scalar("SELECT photo_path FROM users WHERE username = 'kid70'")
    assert "/photos/" in path and path.endswith(".jpg")
    student = next(s for s in client.get("/students", params={"class_id": school.c1, "section_id": school.c1a},
                                         headers=bearer(school.admin)).json() if s["username"] == "kid70")
    image = client.get("/" + student["photo_path"])
    assert image.status_code == 200
    with Image.open(io.BytesIO(image.content)) as img:
        assert max(img.size) <= settings.image_max_dimension
        assert 0x8825 not in img.getexif()  # GPS removed

    # a new photo on re-upload replaces the old one (old file removed after commit)
    data = make_csv([kid(70, photo="spare.jpg", password="")])
    r = upload(client, school.admin, "student", data, section=(school.c1, school.c1a), save=True, photos=zipped)
    assert rows_by_user(r.json())["kid70"]["changes"] == ["photo"]
    assert client.get("/" + student["photo_path"]).status_code in (403, 404)


def test_photo_problems(client, school):
    zipped = make_zip({"bad.jpg": b"\xff\xd8\xff not really a jpeg", "dup.jpg": png_bytes(),
                       "other/dup.jpg": png_bytes(), "notes.txt": b"hello"})
    data = make_csv([kid(80, photo="missing.jpg"), kid(81, photo="bad.jpg"), kid(82, photo="dup.jpg"),
                     kid(83, photo="notes.txt")])
    rows = upload(client, school.admin, "student", data, section=(school.c1, school.c1a), photos=zipped).json()["rows"]
    assert rows[0]["errors"] == ["Photo 'missing.jpg' is not in the photos ZIP."]
    assert rows[1]["errors"] == ["Photo 'bad.jpg': invalid or corrupted image."]
    assert "several files with this name" in rows[2]["errors"][0]
    assert rows[3]["errors"] == ["Photo 'notes.txt': not a JPEG, PNG or WebP image."]

    no_zip = upload(client, school.admin, "student", make_csv([kid(84, photo="a.jpg")]),
                    section=(school.c1, school.c1a))
    assert no_zip.json()["rows"][0]["errors"] == ["Photo 'a.jpg' is listed but no photos ZIP was uploaded."]
    not_zip = upload(client, school.admin, "student", make_csv([kid(85)]), section=(school.c1, school.c1a),
                     photos=b"not a zip")
    assert not_zip.status_code == 400


def test_failed_save_removes_stored_photos(client, school, db, monkeypatch):
    from app.storage import get_store

    before = set(get_store().iter_keys())

    def boom(*_args, **_kwargs):
        raise RuntimeError("database fell over")

    monkeypatch.setattr(import_writer, "apply", boom)
    zipped = make_zip({"p.png": png_bytes()})
    with pytest.raises(RuntimeError):
        upload(client, school.admin, "student", make_csv([kid(90, photo="p.png")]),
               section=(school.c1, school.c1a), save=True, photos=zipped)
    assert set(get_store().iter_keys()) == before
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username = 'kid90'") == 0


# ------------------------------------------------------------ file checks

def test_file_level_problems(client, school, monkeypatch):
    sec = (school.c1, school.c1a)
    xlsx = upload(client, school.admin, "student", b"PK\x03\x04 excel stuff", section=sec)
    assert xlsx.status_code == 400 and "Excel" in xlsx.json()["detail"]
    latin1 = upload(client, school.admin, "student", "username,full_name,email\nkid,Ren\xe9,a@b.co\n".encode("latin-1"),
                    section=sec)
    assert latin1.status_code == 400 and "UTF-8" in latin1.json()["detail"]
    missing = upload(client, school.admin, "student", b"username,full_name\nkid1,Kid\n", section=sec)
    assert missing.status_code == 400 and missing.json()["detail"].startswith("Missing column(s): email")
    assert upload(client, school.admin, "student", b"", section=sec).status_code == 400
    assert upload(client, school.admin, "student", b"username,full_name,email\n\n", section=sec).status_code == 400
    dup = upload(client, school.admin, "student", b"username,full_name,name,email\n", section=sec)
    assert dup.status_code == 400 and "same field" in dup.json()["detail"]

    # Excel habits: BOM, semicolons, blank trailing rows, CRLF
    ok = make_csv([kid(95), kid(96), ["", "", "", "", "", "", "", "", "", "", ""]], delimiter=";", bom=True)
    r = upload(client, school.admin, "student", ok.replace(b"\n", b"\r\n"), section=sec)
    assert r.status_code == 200 and r.json()["summary"]["create"] == 2

    monkeypatch.setattr(import_service, "settings", dataclasses.replace(settings, import_max_rows=1))
    too_many = upload(client, school.admin, "student", make_csv([kid(97), kid(98)]), section=sec)
    assert too_many.status_code == 400 and "more than 1 users" in too_many.json()["detail"]


def test_export_protects_against_spreadsheet_formulas(client, school):
    a = bearer(school.admin)
    assert client.put(f"/students/{school.s1}", json={"full_name": "=HYPERLINK(\"http://evil\")",
                                                      "email": "s1@example.com", "phone": "+91 70000 00001"},
                      headers=a).status_code == 200
    exported = client.get("/imports/users/export", params={"user_type": "student", "class_id": school.c1,
                                                           "section_id": school.c1a}, headers=a)
    text = exported.content.decode("utf-8-sig")
    assert "'=HYPERLINK" in text and "'+91 70000 00001" in text
    # uploading the export again changes nothing (the apostrophes are removed)
    report = upload(client, school.admin, "student", exported.content, section=(school.c1, school.c1a)).json()
    assert rows_by_user(report)["student1"]["action"] == "unchanged"


def test_changes_made_while_saving_abort_the_import(client, school, db, monkeypatch):
    real_hash = import_writer.hash_passwords

    def hash_and_interfere(plan):
        other = Db(pool)  # someone adds a user between the check and the save
        try:
            other.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
                          "VALUES (1, 'kid99', 'x', 'Someone Else', 'teacher')")
        finally:
            other.close()
        return real_hash(plan)

    monkeypatch.setattr(import_writer, "hash_passwords", hash_and_interfere)
    data = make_csv([["student1", "", "Student 1", "s1@example.com", "999999", "ADM1", "1", "", "", "", ""],
                     kid(99)])
    r = upload(client, school.admin, "student", data, section=(school.c1, school.c1a), save=True)
    assert r.status_code == 409 and "changed by someone else" in r.json()["detail"]
    assert db.scalar("SELECT phone FROM users WHERE id = %s", (school.s1,)) is None  # nothing saved


def test_oversized_uploads_are_refused_before_reading(client):
    from fastapi.testclient import TestClient

    from app.main import api

    small = TestClient(BodySizeLimitMiddleware(api, {"/imports/users": 1000}))
    r = small.post("/imports/users", files={"file": ("u.csv", b"x" * 5000, "text/csv")})
    assert r.status_code == 413 and r.json()["detail"].startswith("Upload too large")
    assert small.get("/health").status_code == 200
