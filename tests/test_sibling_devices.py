"""One phone, every brother and sister (migration 0026, services/notifications.py).

Brothers and sisters share a username (migration 0024). The phone is registered
for the child signed in AND their active brothers / sisters, so each child's
pushes arrive whoever is signed in; on a shared phone the title starts with the
child's name and the push says whose it is (`for_user`, the app switches on tap).
"""
from __future__ import annotations

import json

import pytest
from firebase_admin import messaging
from helpers import bearer, login
from test_siblings import PARENT, _ok, add_student

from app.services import notify

TOKEN = {"token": "family-phone", "platform": "android"}


@pytest.fixture
def family(school):
    """Anuj (1 — A), Aman (2 — A), Asha (1 — B) share one username."""
    school.anuj = _ok(add_student(school, PARENT, "anuj-pass", "Anuj Kumar", school.c1, school.c1a), 201)["id"]
    school.aman = _ok(add_student(school, PARENT, "aman-pass", "Aman Kumar", school.c2, school.c2a,
                                  sibling=True), 201)["id"]
    school.asha = _ok(add_student(school, PARENT, "asha-pass", "Asha Kumar", school.c1, school.c1b,
                                  sibling=True), 201)["id"]
    return school


def _phone(db, token="family-phone"):
    return sorted(r["user_id"] for r in db.all("SELECT user_id FROM device_tokens WHERE token = %s", (token,)))


def test_the_phone_is_registered_for_every_brother_and_sister(family, db):
    c = family.client
    anuj = login(c, PARENT, "anuj-pass")
    _ok(c.post("/devices", headers=bearer(anuj), json=TOKEN), 204)
    assert _phone(db) == sorted([family.anuj, family.aman, family.asha])
    # registering again (every app start) changes nothing
    _ok(c.post("/devices", headers=bearer(anuj), json=TOKEN), 204)
    assert db.scalar("SELECT COUNT(*) FROM device_tokens") == 3

    # a child who left gets nothing any more, and is not registered again
    _ok(c.delete(f"/students/{family.asha}?reason=moved", headers=bearer(family.admin)))
    assert _phone(db) == sorted([family.anuj, family.aman])
    _ok(c.post("/devices", headers=bearer(anuj), json=TOKEN), 204)
    assert _phone(db) == sorted([family.anuj, family.aman])

    # a single child (no siblings) and staff: exactly as before, one row
    s1 = login(c, "student1", "studpass")
    _ok(c.post("/devices", headers=bearer(s1), json={"token": "own-phone", "platform": "android"}), 204)
    assert _phone(db, "own-phone") == [family.s1]
    _ok(c.post("/devices", headers=bearer(family.t1), json={"token": "teacher-phone", "platform": "ios"}), 204)
    assert _phone(db, "teacher-phone") == [family.t1_id]


def test_someone_else_signing_in_on_the_phone_takes_it_over(family, db):
    c = family.client
    _ok(c.post("/devices", headers=bearer(login(c, PARENT, "anuj-pass")), json=TOKEN), 204)
    # the phone is handed to another parent: only their child gets pushes on it now
    _ok(c.post("/devices", headers=bearer(login(c, "student1", "studpass")), json=TOKEN), 204)
    assert _phone(db) == [family.s1]


def test_switch_keeps_the_phone_and_sign_out_ends_it(family, db):
    c = family.client
    anuj = login(c, PARENT, "anuj-pass")
    _ok(c.post("/devices", headers=bearer(anuj), json=TOKEN), 204)
    aman = _ok(c.post("/auth/switch-sibling", headers=bearer(anuj), json={"student_id": family.aman}))
    assert _phone(db) == sorted([family.anuj, family.aman, family.asha])
    # sign-out on the phone (the app also sends DELETE /devices/<token>): no child gets pushes there
    _ok(c.post("/auth/logout", headers=bearer(aman), json={"refresh_token": aman["refresh_token"]}))
    assert _phone(db) == []

    # unregistering the token (what the app does on sign-out) clears every child too
    anuj = login(c, PARENT, "anuj-pass")
    _ok(c.post("/devices", headers=bearer(anuj), json=TOKEN), 204)
    assert c.delete("/devices/family-phone").status_code == 204
    assert _phone(db) == []

    # "sign out everywhere" by one child ends that phone for the siblings registered with it too
    _ok(c.post("/devices", headers=bearer(anuj), json=TOKEN), 204)
    s1 = login(c, "student1", "studpass")
    _ok(c.post("/devices", headers=bearer(s1), json={"token": "own-phone", "platform": "android"}), 204)
    assert c.post("/auth/logout-all", headers=bearer(anuj)).status_code in (200, 204)
    assert _phone(db) == [] and _phone(db, "own-phone") == [family.s1]


def test_shared_phone_messages_carry_the_childs_name():
    rows = [
        {"user_id": 1, "token": "shared", "full_name": "Anuj Kumar", "accounts": 2},
        {"user_id": 2, "token": "shared", "full_name": "Aman Kumar", "accounts": 2},
        {"user_id": 3, "token": "alone", "full_name": "Ravi Singh", "accounts": 1},
    ]
    data = {"route": "Notices"}
    out = notify.shared_device_messages([
        (1, "fee", "New fee: Term 2", "₹5,000", {"route": "Fees"}),
        (1, "notice", "Holiday tomorrow", None, data),
        (2, "notice", "Holiday tomorrow", None, data),  # the same news for the brother: one banner
        (3, "notice", "Holiday tomorrow", None, data),  # an only child's phone: unchanged
    ], rows)
    assert out == [
        ("shared", "fee", "Anuj: New fee: Term 2", "₹5,000", {"route": "Fees"}, 1),
        ("shared", "notice", "Anuj, Aman: Holiday tomorrow", None, data, 1),
        ("alone", "notice", "Holiday tomorrow", None, data, None),
    ]


def test_pushes_reach_the_shared_phone_with_the_name(family, db, monkeypatch):
    c = family.client
    _ok(c.post("/devices", headers=bearer(login(c, PARENT, "anuj-pass")), json=TOKEN), 204)
    sent = []

    def send_each(messages, app=None):
        sent.extend(messages)
        return type("Batch", (), {"responses": [type("R", (), {"success": True, "exception": None})()
                                                for _ in messages]})()

    monkeypatch.setattr(notify, "push_configured", lambda: True)
    monkeypatch.setattr(notify, "_firebase_app", lambda: object())
    monkeypatch.setattr(messaging, "send_each", send_each)
    # a fee for Aman while the phone is signed in as Anuj
    _ok(c.post("/fees", headers=bearer(family.admin), json={"student_id": family.aman, "title": "Bus fee",
                                                             "amount": 900}), 201)
    [msg] = sent
    assert msg.token == "family-phone" and msg.notification.title.startswith("Aman: ")
    assert msg.data["for_user"] == str(family.aman) and msg.data["route"] == "Fees"
    assert json.loads(msg.data["params"]) == {}
    # the inbox entry itself has no name prefix (it belongs to Aman's account anyway)
    assert db.scalar("SELECT title FROM notifications WHERE user_id = %s", (family.aman,)).startswith("New fee")
