"""GET /students/username-check (20 Nov 2026, no migration): the app's "Change username"
Verify button. Read-only; the save (PUT /students/{id}/username) checks again."""
from __future__ import annotations

from helpers import bearer, login
from test_siblings import PARENT, _ok, family  # noqa: F401 - fixture


def _check(school, username, student_id=None, who=None):
    params = {"username": username}
    if student_id is not None:
        params["student_id"] = student_id
    return school.client.get("/students/username-check", params=params, headers=bearer(who or school.admin))


def test_free_shared_staff_and_own(family):  # noqa: F811
    s = family
    free = _ok(_check(s, "brand-new-name", s.s1))
    assert free == {"username": "brand-new-name", "available": True, "current": False, "taken_by_staff": False,
                    "shared_with": []}
    # brothers / sisters have it: named with their class, so the office can confirm
    shared = _ok(_check(s, PARENT, s.s1))
    assert shared["available"] is False and shared["taken_by_staff"] is False
    assert [(x["full_name"], x["class_name"], x["section_name"]) for x in shared["shared_with"]] == [
        ("Anuj Kumar", "Class 1", "A"), ("Aman Kumar", "Class 2", "A")]
    # renaming Anuj: his own username is "current", his brother still shares it
    own = _ok(_check(s, PARENT, s.anuj))
    assert own["current"] is True and own["available"] is True
    # a staff account has it: no names, just "pick another"
    staff = _ok(_check(s, "teacher1", s.s1))
    assert (staff["available"], staff["taken_by_staff"], staff["shared_with"]) == (False, True, [])
    # without a student (checking before adding one): the same answers
    assert _ok(_check(s, PARENT))["shared_with"][0]["full_name"] == "Anuj Kumar"
    # spaces around are ignored, like the save
    assert _ok(_check(s, "  brand-new-name  "))["username"] == "brand-new-name"


def test_checks_input_and_roles(family):  # noqa: F811
    s = family
    assert _check(s, "a b").status_code == 400
    assert _check(s, "ab").status_code == 400
    assert _check(s, "ok-name", 999999).status_code == 404
    assert _check(s, "ok-name", s.t1_id).status_code == 404  # a teacher's id is not a student
    assert _check(s, "ok-name", who=s.t1).status_code == 403  # managers only
    assert _check(s, "ok-name", who=login(s.client, PARENT, "anuj-pass")).status_code == 403
    assert s.client.get("/students/username-check", params={"username": "x1x"}).status_code == 401
    # the check changes nothing
    assert _ok(s.client.get(f"/students/{s.s1}", headers=bearer(s.admin)))["username"] == "student1"
