"""Platform operator API (/platform/*): what the owner portal sees and does.

Uses the two filled schools from test_organisations: school A (DEFAULT,
marked "Alphazulu") and school B (ABC, marked "Bravoyankee").
"""
from __future__ import annotations

import pytest
from helpers import bearer
from test_organisations import ALPHA, BRAVO, PLATFORM, _ok, login_to, schools  # noqa: F401 - fixture
from test_password_reset import outbox  # noqa: F401 - fixture used by `schools`

OPERATOR = {**PLATFORM, "X-Platform-Actor": "owner@example.com"}


def test_every_platform_endpoint_needs_the_secret(client):
    calls = [
        ("GET", "/platform/overview"), ("GET", "/platform/organisations"), ("GET", "/platform/organisations/DEFAULT"),
        ("GET", "/platform/organisations/DEFAULT/users"), ("POST", "/platform/organisations/DEFAULT/users/1/password"),
        ("POST", "/platform/organisations/DEFAULT/users/1/sign-out"), ("GET", "/platform/activity"),
        ("GET", "/platform/system"), ("PATCH", "/platform/organisations/DEFAULT"), ("POST", "/platform/organisations"),
    ]
    for method, path in calls:
        for headers in ({}, {"X-Platform-Secret": "wrong"}):
            r = client.request(method, path, headers=headers, json={})
            assert r.status_code == 403, f"{method} {path}: {r.status_code}"
    # a school token is no platform credential
    admin = _ok(login_to(client, "DEFAULT", "admin", "admin123"))
    assert client.get("/platform/overview", headers=bearer(admin)).status_code == 403


def test_overview_adds_up_every_school(client, schools):  # noqa: F811
    o = _ok(client.get("/platform/overview", headers=PLATFORM))
    assert o["schools"] == {"total": 2, "active": 2, "disabled": 0, "new_30d": 2,
                            "needing_attention": o["schools"]["needing_attention"]}
    assert o["people"]["students"] == 2 and o["people"]["teachers"] == 4
    assert o["people"]["by_role"]["principal"] == 2  # seeded principal in A, principal2 in B
    assert o["attendance_today"] == {"marked": 2, "present": 2, "rate": 100.0,
                                     "schools_marked": 2, "schools_expected": 2}
    assert o["staff_checked_in_today"] == 2
    assert o["pending"] == {"fee_claims": 2, "leaves": 2}
    assert o["fees"]["collected"] == 600.0  # one paid 300 bus fee per school
    assert o["fees"]["pending"] == 2 * (1200 + 5000)
    assert o["active_users"]["last_24h"] == 6  # admin, teacher1, student1 in each school
    assert o["logins_24h"]["ok"] >= 6
    for series in ("attendance", "payments", "logins"):
        assert len(o["trends"][series]) == 14
    assert o["trends"]["attendance"][-1] == {"date": o["today"], "marked": 2, "present": 2, "rate": 100.0}
    assert o["trends"]["payments"][-1]["amount"] == o["fees"]["collected_30d"] > 0

    rows = {r["code"]: r for r in o["organisations"]}
    assert set(rows) == {"DEFAULT", "ABC"}
    b = rows["ABC"]
    assert b["name"] == "ABC Public School" and b["is_active"] is True
    assert b["students"] == 1 and b["teachers"] == 2 and b["staff"] == 4
    assert b["attendance_today"]["rate"] == 100.0
    assert len(b["attendance_14d"]) == 14 and b["attendance_14d"][-1] == 100.0 and b["attendance_14d"][0] is None
    assert b["active_users"]["last_24h"] == 3 and b["last_active_at"].endswith("+00:00")
    assert "fee_claims_waiting" in b["alerts"] and "attendance_not_marked" not in b["alerts"]
    assert b["created_at"].endswith("+05:30")
    # the list endpoint returns the same rows
    listed = {r["code"]: r for r in _ok(client.get("/platform/organisations", headers=PLATFORM))}
    assert listed["ABC"]["fees"] == b["fees"]


def test_a_new_school_shows_what_it_still_needs(client):
    _ok(client.post("/platform/organisations", headers=PLATFORM, json={
        "code": "NEW1", "name": "Brand New School", "admin_password": "first-pass-1"}), 201)
    row = {r["code"]: r for r in _ok(client.get("/platform/organisations", headers=PLATFORM))}["NEW1"]
    assert row["users"] == 1 and row["active_users"]["last_30d"] == 0
    assert {"setup_incomplete", "inactive_30d"} <= set(row["alerts"])
    _ok(client.patch("/platform/organisations/NEW1", headers=PLATFORM, json={"is_active": False}))
    row = {r["code"]: r for r in _ok(client.get("/platform/organisations", headers=PLATFORM))}["NEW1"]
    assert row["alerts"] == ["disabled"]


def test_school_detail_is_one_school_only(client, schools):  # noqa: F811
    d = _ok(client.get("/platform/organisations/abc", headers=PLATFORM))
    assert d["organisation"]["code"] == "ABC"
    roles = d["people"]["by_role"]
    assert (roles["admin"]["total"], roles["principal"]["total"], roles["teacher"]["total"],
            roles["student"]["total"]) == (1, 1, 2, 1)
    assert d["people"]["total"] == 5
    assert d["structure"] == {"classes": 1, "sections": 1, "vehicles": 1, "school_timings_set": True}
    assert d["attendance"]["today"] == {"marked": 1, "present": 1, "rate": 100.0}
    assert d["attendance"]["staff_checked_in_today"] == 1
    assert d["pending"] == {"leaves": 1, "fee_claims": 1, "substitutions": 1}
    assert d["fees"]["collected"] == 300.0 and d["fees"]["pending_claims"] == 1
    assert d["enquiries"]["new"] == 1
    assert d["academics"]["exams_upcoming"] == 1 and d["academics"]["notices_30d"] == 1
    assert d["engagement"]["active_users"]["last_24h"] == 3
    assert [m["username"] for m in d["managers"]] == ["admin", "principal2"]
    assert d["managers"][0]["last_seen_at"] is not None
    text = repr(d)
    assert BRAVO in text and ALPHA not in text
    assert client.get("/platform/organisations/NOPE", headers=PLATFORM).status_code == 404


def test_school_users_page_and_search_inside_the_school(client, schools):  # noqa: F811
    r = client.get("/platform/organisations/ABC/users?limit=2", headers=PLATFORM)
    assert r.status_code == 200 and r.headers["x-total-count"] == "5" and len(r.json()) == 2
    second = client.get("/platform/organisations/ABC/users?limit=2&offset=2", headers=PLATFORM).json()
    assert {u["id"] for u in r.json()}.isdisjoint(u["id"] for u in second)

    found = _ok(client.get(f"/platform/organisations/ABC/users?q={BRAVO} Student", headers=PLATFORM))
    assert [(u["username"], u["class_name"], u["section_name"], u["admission_no"]) for u in found] == \
        [("student1", "Class 1", "A", "ADM1")]
    assert found[0]["last_seen_at"] is not None
    teachers = _ok(client.get("/platform/organisations/ABC/users?role=teacher", headers=PLATFORM))
    assert {u["full_name"] for u in teachers} == {f"{BRAVO} Teacher", f"{BRAVO} Teacher Two"}
    assert _ok(client.get("/platform/organisations/ABC/users?q=%25", headers=PLATFORM)) == []  # "%" is literal
    assert _ok(client.get(f"/platform/organisations/ABC/users?q={ALPHA}", headers=PLATFORM)) == []
    assert client.get("/platform/organisations/ABC/users?role=wizard", headers=PLATFORM).status_code == 422


def test_operator_sets_a_password_and_signs_users_out(client, schools):  # noqa: F811
    a, b = schools
    old = bearer(b.t)
    assert client.get("/auth/me", headers=old).status_code == 200
    # the id must belong to the school named in the path
    assert client.post(f"/platform/organisations/DEFAULT/users/{b.teacher}/password", headers=PLATFORM,
                       json={"new_password": "operator-set-1"}).status_code == 404
    assert client.post(f"/platform/organisations/ABC/users/{b.teacher}/password", headers=PLATFORM,
                       json={"new_password": "short"}).status_code == 422
    done = _ok(client.post(f"/platform/organisations/ABC/users/{b.teacher}/password", headers=OPERATOR,
                           json={"new_password": "operator-set-1"}))
    assert done["username"] == "teacher1" and done["must_change_password"] is True
    assert client.get("/auth/me", headers=old).status_code == 401
    assert login_to(client, "ABC", "teacher1", "teachpass").status_code == 401
    fresh = _ok(login_to(client, "ABC", "teacher1", "operator-set-1"))
    assert fresh["must_change_password"] is True
    _ok(login_to(client, "DEFAULT", "teacher1", "teachpass"))  # school A's teacher1 untouched

    student = bearer(b.s)
    assert client.get("/auth/me", headers=student).status_code == 200
    _ok(client.post(f"/platform/organisations/ABC/users/{b.student}/sign-out", headers=OPERATOR))
    assert client.get("/auth/me", headers=student).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": b.s["refresh_token"]}).status_code == 401

    trail = _ok(client.get("/platform/activity?org=ABC&action=platform.", headers=PLATFORM))["items"]
    assert [i["action"] for i in trail] == ["platform.user_signed_out", "platform.user_password_set"]
    assert all(i["details"]["operator"] == "owner@example.com" for i in trail)
    assert trail[1]["details"]["username"] == "teacher1" and trail[1]["organisation"]["code"] == "ABC"


def test_activity_pages_and_filters(client, schools):  # noqa: F811
    first = _ok(client.get("/platform/activity?limit=5", headers=PLATFORM))
    assert len(first["items"]) == 5 and first["next_before_id"] == first["items"][-1]["id"]
    second = _ok(client.get(f"/platform/activity?limit=5&before_id={first['next_before_id']}", headers=PLATFORM))
    assert max(i["id"] for i in second["items"]) < min(i["id"] for i in first["items"])

    everything = _ok(client.get("/platform/activity?limit=200", headers=PLATFORM))
    assert everything["next_before_id"] is None
    codes = {i["organisation"]["code"] for i in everything["items"] if i["organisation"]}
    assert codes == {"DEFAULT", "ABC"}
    only_b = _ok(client.get("/platform/activity?org=abc&limit=200", headers=PLATFORM))["items"]
    assert only_b and all(i["organisation"]["code"] == "ABC" for i in only_b)
    fees = _ok(client.get("/platform/activity?action=fee&limit=200", headers=PLATFORM))["items"]
    assert fees and all(i["action"].startswith("fee") for i in fees)
    assert any(i["actor"] and i["actor"]["username"] == "admin" for i in fees)
    assert _ok(client.get("/platform/activity?action=fee_%25", headers=PLATFORM))["items"] == []
    assert client.get("/platform/activity?org=NOPE", headers=PLATFORM).status_code == 404


@pytest.mark.parametrize("actor, recorded", [("owner@example.com", "owner@example.com"),
                                             ("bad<actor>", None), ("", None)])
def test_school_changes_name_the_operator(client, actor, recorded):
    headers = {**PLATFORM, "X-Platform-Actor": actor} if actor else PLATFORM
    _ok(client.post("/platform/organisations", headers=headers, json={
        "code": "OPS", "name": "Ops School", "admin_password": "first-pass-1"}), 201)
    _ok(client.patch("/platform/organisations/OPS", headers=headers, json={"name": "Ops School 2"}))
    items = _ok(client.get("/platform/activity?org=OPS&action=organisation.", headers=PLATFORM))["items"]
    assert [i["action"] for i in items] == ["organisation.update", "organisation.create"]
    assert [i["details"].get("operator") for i in items] == [recorded, recorded]
    assert items[0]["details"]["name"] == "Ops School 2"


def test_system_status(client):
    s = _ok(client.get("/platform/system", headers=PLATFORM))
    assert s["database"]["migration"] == "0010_platform_indexes"
    assert s["database"]["ping_ms"] >= 0 and s["database"]["size_bytes"] > 0
    assert s["database"]["tables"] and all(t["bytes"] >= 0 for t in s["database"]["tables"])
    assert s["settings"]["default_org_code"] == "DEFAULT"
    assert "secret" not in repr(s).lower()
