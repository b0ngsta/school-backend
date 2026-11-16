"""Brothers and sisters: student accounts of one school that share a username
(migration 0024, services/siblings.py). The password says which child a
sign-in opens; "Switch child" moves between them without a password."""
from __future__ import annotations

import pymysql
import pytest
from conftest import TEST_DB_URL
from dbutil import alembic, drop_database, recreate_database, with_database
from helpers import bearer, client_from, login, post_login
from test_password_reset import code_from, outbox  # noqa: F401 - fixture
from test_user_import import kid, make_csv, rows_by_user, upload

from app.core.errors import DuplicateKey

PARENT = "9876543210"  # the office uses a parent's mobile number; the code does not care what it is
PLATFORM = {"X-Platform-Secret": "test-platform-secret"}


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def add_student(school, username, password, name, cls, sec, *, sibling=None, who=None, **extra):
    body = {"username": username, "password": password, "full_name": name, "class_id": cls, "section_id": sec,
            **extra}
    if sibling is not None:
        body["sibling"] = sibling
    return school.client.post("/students", json=body, headers=bearer(who or school.admin))


@pytest.fixture
def family(school):
    """Anuj (Class 1 — A) and Aman (Class 2 — A) share one username, each with a password."""
    school.anuj = _ok(add_student(school, PARENT, "anuj-pass", "Anuj Kumar", school.c1, school.c1a, roll_no="21"),
                      201)["id"]
    school.aman = _ok(add_student(school, PARENT, "aman-pass", "Aman Kumar", school.c2, school.c2a, sibling=True),
                      201)["id"]
    return school


# ------------------------------------------------------------ the username rule

def test_sharing_a_username_must_be_confirmed(school, db):
    first = _ok(add_student(school, PARENT, "anuj-pass", "Anuj Kumar", school.c1, school.c1a), 201)["id"]
    # the same request as always: the same 409 as always, now saying who has the username
    r = add_student(school, PARENT, "aman-pass", "Aman Kumar", school.c2, school.c2a)
    assert r.status_code == 409 and r.json() == {
        "detail": "Username already exists", "code": "username_shared",
        "siblings": [{"id": first, "full_name": "Anuj Kumar", "class_name": "Class 1", "section_name": "A",
                      "left": False}]}
    assert add_student(school, PARENT, "aman-pass", "Aman Kumar", school.c2, school.c2a,
                       sibling=False).status_code == 409
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username = %s", (PARENT,)) == 1
    # confirmed: a second account with the same username, in the same school
    second = _ok(add_student(school, PARENT, "aman-pass", "Aman Kumar", school.c2, school.c2a, sibling=True), 201)
    assert second["username"] == PARENT and second["id"] != first
    # the class teacher who may add students to a section may add a brother or sister there too
    _ok(add_student(school, PARENT, "arav-pass", "Arav Kumar", school.c1, school.c1a, sibling=True, who=school.t1),
        201)
    # `sibling` changes nothing for a free username
    _ok(add_student(school, "only.child", "only-pass", "Only Child", school.c1, school.c1a, sibling=True), 201)
    audit = db.all("SELECT action FROM audit_logs WHERE action = 'student.create'")
    assert len(audit) == 4 + 4  # the school's four students and these four


def test_a_staff_username_is_never_shared(school, db):
    h = bearer(school.admin)
    # a student cannot take a staff username, confirmed or not (and the reply offers no "sibling")
    for flag in (None, True):
        r = add_student(school, "teacher1", "kid-pass1", "Kid", school.c1, school.c1a, sibling=flag)
        assert r.status_code == 409 and r.json() == {"detail": "Username already exists"}
    # staff cannot take a student's username, shared or not
    _ok(add_student(school, "student1", "twin-pass", "Twin", school.c1, school.c1b, sibling=True), 201)
    for username in ("student1", "student2"):
        r = school.client.post("/users", headers=h, json={"username": username, "password": "teachpass",
                                                           "full_name": "T", "user_type": "teacher"})
        assert r.status_code == 409 and r.json() == {"detail": "Username already exists"}
    r = school.client.post("/vehicles", headers=h, json={
        "vehicle_no": "RJ14 AB 0001", "driver_name": "D", "new_driver": {"username": "student1",
                                                                         "password": "drive123"}})
    assert r.status_code == 409 and db.scalar("SELECT COUNT(*) FROM vehicles") == 0
    # staff usernames stay unique among staff, also for an account that has left
    assert school.client.post("/users", headers=h, json={"username": "teacher1", "password": "teachpass",
                                                         "full_name": "T", "user_type": "driver"}).status_code == 409
    _ok(school.client.delete(f"/users/{school.t2_id}", headers=h))
    r = add_student(school, "teacher2", "kid-pass1", "Kid", school.c1, school.c1a, sibling=True)
    assert r.status_code == 409 and "marked as left" in r.json()["detail"] and "code" not in r.json()
    # the database itself refuses a second staff account with one username, and lets students share
    with pytest.raises(DuplicateKey) as refused:
        db.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
                   "VALUES (1, 'TEACHER1', 'x', 'T', 'coordinator')")
    assert refused.value.key == "uq_users_org_staff_username" and refused.value.detail == "Username already exists"
    db.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
               "VALUES (1, 'student2', 'x', 'S', 'student')")


def test_a_left_student_keeps_the_username_and_a_sibling_can_still_join(school):
    h = bearer(school.admin)
    _ok(school.client.delete(f"/students/{school.s1}", headers=h))
    r = add_student(school, "student1", "new-pass1", "New Kid", school.c1, school.c1a)
    assert r.status_code == 409 and r.json()["code"] == "username_shared"
    assert r.json()["detail"] == ("Username already exists: it belongs to Student 1, who is marked as left. "
                                  "Reactivate that account, or choose another username.")
    assert r.json()["siblings"][0]["left"] is True
    new = _ok(add_student(school, "student1", "new-pass1", "New Kid", school.c1, school.c1a, sibling=True), 201)["id"]
    assert login(school.client, "student1", "new-pass1")["user_id"] == new
    # the one who left comes back as a brother / sister
    _ok(school.client.post(f"/students/{school.s1}/reactivate", headers=h))
    assert login(school.client, "student1", "studpass")["user_id"] == school.s1
    assert [s["id"] for s in _ok(school.client.get("/student/siblings", headers=bearer(
        login(school.client, "student1", "studpass"))))] == [new]


def test_the_office_gives_an_existing_student_a_sibling_username(school, db):
    c, h = school.client, bearer(school.admin)
    url = f"/students/{school.s4}/username"
    assert c.put(url, headers=bearer(school.t3), json={"username": "student1"}).status_code == 403  # office only
    r = c.put(url, headers=h, json={"username": "student1"})
    assert r.status_code == 409 and r.json()["code"] == "username_shared"
    assert [s["full_name"] for s in r.json()["siblings"]] == ["Student 1"]
    assert c.put(url, headers=h, json={"username": "teacher1", "sibling": True}).status_code == 409
    assert c.put(url, headers=h, json={"username": "a b"}).status_code == 422
    assert c.put("/students/999999/username", headers=h, json={"username": "whoever"}).status_code == 404
    assert c.put(f"/students/{school.t1_id}/username", headers=h, json={"username": "whoever"}).status_code == 404

    s4 = login(c, "student4", "studpass")
    done = _ok(c.put(url, headers=h, json={"username": "student1", "sibling": True}))
    assert done["username"] == "student1" and done["changed"] is True
    assert [s["id"] for s in done["siblings"]] == [school.s1]
    _ok(c.get("/auth/me", headers=bearer(s4)))  # still signed in; the password is the same
    assert c.post("/auth/login", json={"username": "student4", "password": "studpass"}).status_code == 401
    assert login(c, "student1", "studpass")["user_id"] == school.s1  # same password: the older account
    assert [s["id"] for s in _ok(c.get("/student/siblings", headers=bearer(s4)))] == [school.s1]
    details = db.one("SELECT details FROM audit_logs WHERE action = 'student.username_change'")["details"]
    assert '"from": "student4"' in details and '"to": "student1"' in details
    # a free username needs no confirmation; the same name again changes nothing
    assert _ok(c.put(url, headers=h, json={"username": "student44"}))["siblings"] == []
    assert _ok(c.put(url, headers=h, json={"username": "student44"}))["changed"] is False
    assert login(c, "student44", "studpass")["user_id"] == school.s4


# ------------------------------------------------------------ sign-in

def test_the_password_decides_which_child_signs_in(family):
    c = family.client
    aman = _ok(c.post("/auth/login", json={"username": PARENT, "password": "aman-pass"}))
    anuj = _ok(c.post("/auth/login", json={"username": PARENT, "password": "anuj-pass"}))
    assert (anuj["user_id"], anuj["full_name"]) == (family.anuj, "Anuj Kumar")
    assert (aman["user_id"], aman["full_name"]) == (family.aman, "Aman Kumar")
    # an ordinary sign-in in every other way
    single = login(c, "student1", "studpass")
    assert set(aman) == set(single) and aman["user_type"] == "student" and aman["org_code"] == "DEFAULT"
    assert _ok(c.get("/student/me", headers=bearer(aman)))["full_name"] == "Aman Kumar"
    assert _ok(c.get("/auth/me", headers=bearer(anuj)))["username"] == PARENT
    assert _ok(c.post("/auth/refresh", json={"refresh_token": anuj["refresh_token"]}))["user_id"] == family.anuj


def test_a_wrong_password_looks_like_any_wrong_password(family):
    c = family.client
    shared = c.post("/auth/login", json={"username": PARENT, "password": "nope"})
    single = c.post("/auth/login", json={"username": "student1", "password": "nope"})
    unknown = c.post("/auth/login", json={"username": "ghost", "password": "nope"})
    assert shared.status_code == single.status_code == unknown.status_code == 401
    assert shared.json() == single.json() == unknown.json() == {"detail": "Invalid username or password"}
    assert shared.headers.get("www-authenticate") == single.headers.get("www-authenticate")


def test_shared_username_is_throttled_like_one_account(family, db):
    # one failure is counted per try (not one per child): the usual 5 tries from one address
    attacker = client_from("203.0.113.50")
    for _ in range(5):
        assert attacker.post("/auth/login", json={"username": PARENT, "password": "x"}).status_code == 401
    assert db.scalar("SELECT COUNT(*) FROM auth_login_attempts WHERE username = %s AND success = 0",
                     (f"1:{PARENT}",)) == 5
    assert attacker.post("/auth/login", json={"username": PARENT, "password": "aman-pass"}).status_code == 429
    # the parent's own phone (an app that can show a CAPTCHA) is asked for one, whichever child it is
    phone = client_from("203.0.113.51")
    r = phone.post("/auth/login", json={"username": PARENT, "password": "anuj-pass", "captcha_supported": True})
    assert r.status_code == 428 and r.json()["code"] == "captcha_required"
    assert _ok(post_login(phone, {"username": PARENT, "password": "anuj-pass"}))["user_id"] == family.anuj


def test_siblings_with_the_same_password_open_the_older_account(school):
    a = _ok(add_student(school, PARENT, "same-pass", "Anuj Kumar", school.c1, school.c1a), 201)["id"]
    b = _ok(add_student(school, PARENT, "same-pass", "Aman Kumar", school.c2, school.c2a, sibling=True), 201)["id"]
    for _ in range(3):
        tokens = login(school.client, PARENT, "same-pass")
        assert tokens["user_id"] == a < b
    # the other child is one tap away
    assert [s["id"] for s in _ok(school.client.get("/student/siblings", headers=bearer(tokens)))] == [b]


def test_a_left_sibling_is_skipped(family, db):
    c, h = family.client, bearer(family.admin)
    _ok(c.delete(f"/students/{family.anuj}", headers=h))
    # the one who left: the same 403 as any left account; the other child is not affected
    r = c.post("/auth/login", json={"username": PARENT, "password": "anuj-pass"})
    assert r.status_code == 403 and "marked as left" in r.json()["detail"]
    aman = login(c, PARENT, "aman-pass")
    assert aman["user_id"] == family.aman
    assert _ok(c.get("/student/siblings", headers=bearer(aman))) == []
    assert c.post("/auth/switch-sibling", headers=bearer(aman), json={"student_id": family.anuj}).status_code == 404
    assert c.post("/auth/login", json={"username": PARENT, "password": "nope"}).status_code == 401
    # same password on both: the active child signs in, the left one is never chosen
    _ok(c.put(f"/users/{family.aman}/password", headers=h, json={"new_password": "anuj-pass",
                                                                 "must_change_password": False}))
    assert login(c, PARENT, "anuj-pass")["user_id"] == family.aman
    # a disabled (not left) account answers as it always did
    db.execute("UPDATE users SET is_active = 0 WHERE id = %s", (family.aman,))
    db.execute("UPDATE users SET is_active = 0, left_on = NULL WHERE id = %s", (family.anuj,))
    r = c.post("/auth/login", json={"username": PARENT, "password": "anuj-pass"})
    assert r.status_code == 403 and r.json() == {"detail": "Account disabled"}


# ------------------------------------------------------------ the list and the switch

def test_siblings_list(family):
    c, h = family.client, bearer(family.admin)
    _ok(c.post(f"/users/{family.aman}/photo", headers=h,
               files={"file": ("p.png", __import__("helpers").png_bytes(), "image/png")}))
    anuj = login(c, PARENT, "anuj-pass")
    listed = _ok(c.get("/student/siblings", headers=bearer(anuj)))
    assert [set(s) for s in listed] == [{"id", "full_name", "class_name", "section_name", "roll_no", "photo_path"}]
    [aman] = listed
    assert (aman["id"], aman["full_name"], aman["class_name"], aman["section_name"], aman["roll_no"]) == \
        (family.aman, "Aman Kumar", "Class 2", "A", None)
    assert aman["photo_path"].startswith("uploads/photos/") and "sig=" in aman["photo_path"]  # signed like every photo
    [back] = _ok(c.get("/student/siblings", headers=bearer(login(c, PARENT, "aman-pass"))))
    assert (back["id"], back["roll_no"], back["photo_path"]) == (family.anuj, "21", None)
    # an only child has none; staff have no such list
    assert _ok(c.get("/student/siblings", headers=bearer(login(c, "student1", "studpass")))) == []
    assert c.get("/student/siblings", headers=h).status_code == 403
    assert c.get("/student/siblings").status_code == 401


def test_switch_child(family, db):
    c = family.client
    anuj = login(c, PARENT, "anuj-pass")
    _ok(c.post("/devices", headers=bearer(anuj), json={"token": "phone-token-1", "platform": "android"}), 204)
    aman = _ok(c.post("/auth/switch-sibling", headers=bearer(anuj), json={"student_id": family.aman}))
    # the same shape as a sign-in, for the other child, without a password
    assert set(aman) == set(anuj)
    assert (aman["user_id"], aman["full_name"], aman["user_type"]) == (family.aman, "Aman Kumar", "student")
    assert aman["features"] == anuj["features"] and aman["org_code"] == "DEFAULT"
    assert "theme_color" in aman and aman["must_change_password"] is False
    assert _ok(c.get("/student/me", headers=bearer(aman)))["full_name"] == "Aman Kumar"
    # the first child's sign-in ended: one session on the phone, as after any sign-in
    assert c.get("/auth/me", headers=bearer(anuj)).status_code == 401
    assert c.post("/auth/refresh", json={"refresh_token": anuj["refresh_token"]}).status_code == 401
    assert db.scalar("SELECT revoke_reason FROM auth_sessions WHERE user_id = %s", (family.anuj,)) == "switch_sibling"
    assert db.scalar("SELECT COUNT(*) FROM device_tokens") == 0  # the phone registers again as Aman
    _ok(c.post("/devices", headers=bearer(aman), json={"token": "phone-token-1", "platform": "android"}), 204)
    assert db.scalar("SELECT user_id FROM device_tokens WHERE token = 'phone-token-1'") == family.aman
    audit = db.one("SELECT actor_id, entity_id, details FROM audit_logs WHERE action = 'auth.switch_sibling'")
    assert (audit["actor_id"], audit["entity_id"]) == (family.anuj, str(family.aman))
    assert f'"from": {family.anuj}' in audit["details"] and f'"to": {family.aman}' in audit["details"]
    # and back again
    again = _ok(c.post("/auth/switch-sibling", headers=bearer(aman), json={"student_id": family.anuj}))
    assert again["user_id"] == family.anuj and c.get("/auth/me", headers=bearer(aman)).status_code == 401
    assert _ok(c.post("/auth/refresh", json={"refresh_token": again["refresh_token"]}))["user_id"] == family.anuj


def test_switch_only_to_a_brother_or_sister(family):
    c = family.client
    anuj = login(c, PARENT, "anuj-pass")
    h = bearer(anuj)
    for target in (family.s1,            # another family's child
                   family.t1_id,         # a teacher
                   family.anuj,          # yourself
                   999999):
        r = c.post("/auth/switch-sibling", headers=h, json={"student_id": target})
        assert r.status_code == 404 and r.json() == {"detail": "Student not found"}, target
    assert c.post("/auth/switch-sibling", headers=h, json={"student_id": "x"}).status_code == 422
    assert c.post("/auth/switch-sibling", headers=h, json={}).status_code == 422
    assert c.post("/auth/switch-sibling", json={"student_id": family.aman}).status_code == 401
    # staff cannot switch into a student, even one whose id they know
    for staff in (family.admin, family.t1):
        assert c.post("/auth/switch-sibling", headers=bearer(staff),
                      json={"student_id": family.aman}).status_code == 403
    _ok(c.get("/auth/me", headers=h))  # refused switches end nothing


def test_switch_shows_the_other_childs_pending_password_change(family):
    c = family.client
    _ok(c.put(f"/users/{family.aman}/password", headers=bearer(family.admin), json={"new_password": "office-set"}))
    anuj = login(c, PARENT, "anuj-pass")
    aman = _ok(c.post("/auth/switch-sibling", headers=bearer(anuj), json={"student_id": family.aman}))
    assert aman["must_change_password"] is True


def test_the_same_username_in_another_school_is_not_a_sibling(family):
    c = family.client
    _ok(c.post("/platform/organisations", headers=PLATFORM, json={
        "code": "ABC", "name": "ABC Public School", "admin_password": "abc-admin-pass"}), 201)
    admin_b = _ok(c.post("/auth/login", json={"org_code": "ABC", "username": "admin", "password": "abc-admin-pass"}))
    hb = bearer(admin_b)
    cls = _ok(c.post("/classes", headers=hb, json={"name": "Class 1"}), 201)["id"]
    sec = _ok(c.post(f"/classes/{cls}/sections", headers=hb, json={"name": "A"}), 201)["id"]
    # the same username is free in school B: no confirmation, no link to school A's children
    other = _ok(c.post("/students", headers=hb, json={"username": PARENT, "password": "anuj-pass",
                                                      "full_name": "Other Family", "class_id": cls,
                                                      "section_id": sec}), 201)["id"]
    b = _ok(c.post("/auth/login", json={"org_code": "ABC", "username": PARENT, "password": "anuj-pass"}))
    assert b["user_id"] == other and _ok(c.get("/student/siblings", headers=bearer(b))) == []
    for target in (family.anuj, family.aman):
        assert c.post("/auth/switch-sibling", headers=bearer(b), json={"student_id": target}).status_code == 404
    anuj = login(c, PARENT, "anuj-pass")  # school A (the default school)
    assert anuj["user_id"] == family.anuj
    assert [s["id"] for s in _ok(c.get("/student/siblings", headers=bearer(anuj)))] == [family.aman]
    assert c.post("/auth/switch-sibling", headers=bearer(anuj), json={"student_id": other}).status_code == 404
    # school A's password never opens school B's account with that username
    assert c.post("/auth/login", json={"org_code": "ABC", "username": PARENT,
                                       "password": "aman-pass"}).status_code == 401
    # a disabled school: no list, no switch
    _ok(c.patch("/platform/organisations/ABC", headers=PLATFORM, json={"is_active": False}))
    assert c.get("/student/siblings", headers=bearer(b)).status_code == 401
    assert c.post("/auth/switch-sibling", headers=bearer(b), json={"student_id": other}).status_code == 401


# ------------------------------------------------------------ forgot password, office tools

def test_forgot_password_with_a_shared_username(family, outbox):  # noqa: F811
    c, h = family.client, bearer(family.admin)
    for sid, name in ((family.anuj, "Anuj Kumar"), (family.aman, "Aman Kumar")):
        _ok(c.put(f"/students/{sid}", headers=h, json={"full_name": name, "email": "parent@example.com"}))
    _ok(c.put(f"/students/{family.s1}", headers=h, json={"full_name": "Student 1", "email": "one@example.com"}))
    shared = c.post("/auth/forgot-password", json={"login": PARENT})
    single = c.post("/auth/forgot-password", json={"login": "student1"})
    unknown = c.post("/auth/forgot-password", json={"login": "ghost"})
    # the reply says nothing about which, or how many, accounts there are
    assert shared.status_code == single.status_code == unknown.status_code == 200
    assert shared.json() == single.json() == unknown.json()
    # the family's address gets one note (no code): the office sets the password
    [note] = outbox.to("parent@example.com")
    assert "Anuj Kumar" in note.text and "Aman Kumar" in note.text and "school office" in note.text
    assert not __import__("re").search(r"\b\d{6}\b", note.text)
    for guess in ("000000", "123456"):
        assert c.post("/auth/reset-password", json={"login": PARENT, "code": guess,
                                                    "new_password": "brand-new-1"}).status_code == 400
    assert login(c, PARENT, "anuj-pass")["user_id"] == family.anuj  # nothing changed
    # an account with its own username resets as always
    code = code_from(outbox.to("one@example.com")[0])
    assert _ok(c.post("/auth/reset-password", json={"login": "student1", "code": code,
                                                    "new_password": "brand-new-1"}))["user_id"] == family.s1
    # the office sets one child's password; the other child's stays
    _ok(c.put(f"/users/{family.aman}/password", headers=h, json={"new_password": "from-office",
                                                                 "must_change_password": False}))
    assert login(c, PARENT, "from-office")["user_id"] == family.aman
    assert login(c, PARENT, "anuj-pass")["user_id"] == family.anuj
    # when a brother has left, the remaining child is an ordinary single account again
    _ok(c.delete(f"/students/{family.anuj}", headers=h))
    _ok(c.post("/auth/forgot-password", json={"login": PARENT}))
    code = code_from(outbox.to("parent@example.com")[-1])
    assert _ok(c.post("/auth/reset-password", json={"login": PARENT, "code": code,
                                                    "new_password": "brand-new-2"}))["user_id"] == family.aman


def test_command_line_asks_which_account(family, db, capsys):
    from app import cli

    # one account with the username: as before
    cli.revoke_sessions(db, "student1")
    assert "All sessions of 'student1' revoked." in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        cli.revoke_sessions(db, PARENT)
    message = str(stop.value)
    assert "2 accounts share the username" in message
    assert f"--id {family.anuj}  Anuj Kumar (student, Class 1 A)" in message
    assert f"--id {family.aman}  Aman Kumar (student, Class 2 A)" in message
    anuj, aman = login(family.client, PARENT, "anuj-pass"), login(family.client, PARENT, "aman-pass")
    cli.revoke_sessions(db, PARENT, family.aman)
    assert family.client.get("/auth/me", headers=bearer(aman)).status_code == 401
    _ok(family.client.get("/auth/me", headers=bearer(anuj)))
    cli.list_sessions(db, PARENT, family.anuj)
    assert "active until" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="No account with id"):
        cli.revoke_sessions(db, PARENT, family.s1)
    with pytest.raises(SystemExit, match="No user named"):
        cli.list_sessions(db, "ghost")


def test_import_updates_the_child_of_that_section_and_never_adds_a_sibling(family, db):
    c = family.client
    header = ["username", "full_name", "email", "roll_no"]
    # Anuj is in 1-A: his row in 1-A's file is his, although Aman has the username too
    data = make_csv([[PARENT, "Anuj K. Kumar", "parent@example.com", "22"]], header=header)
    report = upload(c, family.t1, "student", data, section=(family.c1, family.c1a), save=True).json()
    assert report["rows"][0]["action"] == "update" and report["rows"][0]["user_id"] == family.anuj
    assert db.scalar("SELECT full_name FROM users WHERE id = %s", (family.anuj,)) == "Anuj K. Kumar"
    assert db.scalar("SELECT full_name FROM users WHERE id = %s", (family.aman,)) == "Aman Kumar"
    # in a section where neither is: skipped with the reason, nothing is created
    report = upload(c, family.admin, "student", make_csv([kid(70, username=PARENT)]),
                    section=(family.c1, family.c1b), save=True)
    assert report.status_code == 422
    [error] = rows_by_user(report.json())[PARENT]["errors"]
    assert error.startswith(f"Username '{PARENT}' is shared by brothers and sisters in other classes: ")
    assert "Anuj K. Kumar (Class 1-A)" in error and "Aman Kumar (Class 2-A)" in error and "Add student" in error
    # twins in one section: the file cannot say which one a row is
    _ok(add_student(family, PARENT, "twin-pass", "Arav Kumar", family.c1, family.c1a, sibling=True), 201)
    report = upload(c, family.admin, "student", data, section=(family.c1, family.c1a)).json()
    assert "is shared by 2 students of this section (Anuj K. Kumar, Arav Kumar)" in report["rows"][0]["errors"][0]
    # a staff import still reports the username as a student's
    report = upload(c, family.admin, "teacher", make_csv(
        [[PARENT, "teachpass", "T", "t@example.com"]], header=["username", "password", "full_name", "email"])).json()
    assert report["rows"][0]["errors"] == [f"Username '{PARENT}' is already used by a student account."]
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username = %s", (PARENT,)) == 3


def test_owner_portal_lists_both_children(family):
    c = family.client
    found = _ok(c.get(f"/platform/organisations/DEFAULT/users?q={PARENT}", headers=PLATFORM))
    assert [(u["id"], u["username"], u["full_name"], u["class_name"]) for u in found] == [
        (family.aman, PARENT, "Aman Kumar", "Class 2"), (family.anuj, PARENT, "Anuj Kumar", "Class 1")]
    # its actions go by id: one child's password, the other untouched
    _ok(c.post(f"/platform/organisations/DEFAULT/users/{family.aman}/password", headers=PLATFORM,
               json={"new_password": "hq-set-pass", "must_change_password": False}))
    assert login(c, PARENT, "hq-set-pass")["user_id"] == family.aman
    assert login(c, PARENT, "anuj-pass")["user_id"] == family.anuj
    listed = _ok(c.get("/students", params={"q": PARENT}, headers=bearer(family.admin)))
    assert {s["id"] for s in listed} == {family.anuj, family.aman}


# ------------------------------------------------------------ the migration

def test_downgrade_waits_until_no_username_is_shared():
    url = with_database(TEST_DB_URL, "mig_siblings")
    recreate_database(url)
    conn = None
    try:
        alembic(url)
        from urllib.parse import urlparse
        p = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
        conn = pymysql.connect(host=p.hostname, port=p.port or 3306, user=p.username, password=p.password or "",
                               database="mig_siblings", autocommit=True)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) VALUES "
                        "(1, '9876543210', 'x', 'Anuj', 'student'), (1, '9876543210', 'x', 'Aman', 'student')")
        with pytest.raises(RuntimeError, match=r"(?s)share a username.*school DEFAULT, username '9876543210': "
                                               r"Anuj \(id \d+\), Aman \(id \d+\)"):
            alembic(url, "downgrade", "0023_live_tracking")
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()[0] == "0024_sibling_usernames"  # nothing was changed
            cur.execute("UPDATE users SET username = 'aman' WHERE full_name = 'Aman'")
        alembic(url, "downgrade", "0023_live_tracking")
        with conn.cursor() as cur, pytest.raises(pymysql.err.IntegrityError, match="uq_users_org_username"):
            cur.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
                        "VALUES (1, 'aman', 'x', 'Again', 'student')")
        alembic(url)  # and up again
    finally:
        if conn:
            conn.close()
        drop_database(url)
