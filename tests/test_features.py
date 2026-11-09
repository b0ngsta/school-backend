"""Per-school feature flags (migration 0019): HQ switches a module off for one
school; every user of that school loses it (403 feature_disabled), the other
school is untouched, and switching it back on brings everything back.

Uses the two filled schools from test_organisations (A = DEFAULT, B = ABC).
"""
from __future__ import annotations

from helpers import bearer
from test_organisations import PLATFORM, _ok, login_to, schools  # noqa: F401 - fixture
from test_password_reset import outbox  # noqa: F401 - fixture used by `schools`

from app.core.features import CATALOGUE, FEATURES

OPERATOR = {**PLATFORM, "X-Platform-Actor": "owner@example.com"}

# feature -> (role login, endpoints) that must answer 403 when it is off
GATED = {
    "attendance": [("teacher1", "teachpass", ["/attendance/stats"]),
                   ("student1", "studpass", ["/student/attendance", "/student/attendance/overview"])],
    "fees": [("admin", None, ["/fees", "/fees/stats", "/fees/claims", "/transactions", "/fee-claim-forms",
                              "/fee-claim-forms/stats"]),
             ("student1", "studpass", ["/student/fees", "/fees/claims/mine", "/student/fee-claims"])],
    "transport": [("admin", None, ["/vehicles", "/vehicles/stats"]),
                  ("student1", "studpass", ["/student/transport"])],
    "lesson_plans": [("teacher1", "teachpass", ["/lessons"])],
    "salary": [("admin", None, ["/salaries"]), ("teacher1", "teachpass", ["/salaries/mine"])],
}


def _token(client, code, username, password, admin):
    if username == "admin":
        return admin
    return _ok(login_to(client, code, username, password))


def _patch(client, code, changes, note=None):
    return client.patch(f"/platform/organisations/{code}/features", headers=OPERATOR,
                        json={"changes": changes, **({"note": note} if note else {})})


def test_catalogue_and_defaults(client, schools):  # noqa: F811
    cat = _ok(client.get("/platform/features", headers=PLATFORM))
    assert [f["key"] for f in cat] == [f.key for f in CATALOGUE]
    assert set(FEATURES) == set(GATED)
    assert all(f["default"] is True for f in cat)
    rows = _ok(client.get("/platform/organisations/ABC/features", headers=PLATFORM))
    assert all(r["enabled"] and r["source"] == "default" for r in rows)
    detail = _ok(client.get("/platform/organisations/ABC", headers=PLATFORM))
    assert [f["key"] for f in detail["features"]] == [f.key for f in CATALOGUE]
    me = _ok(client.get("/auth/me", headers=bearer(schools[1].admin)))
    assert me["features"] == {f.key: True for f in CATALOGUE}
    login = _ok(login_to(client, "ABC", "teacher1", "teachpass"))
    assert login["features"] == {f.key: True for f in CATALOGUE}


def test_switching_each_feature_off_hides_it_in_one_school_only(client, schools):  # noqa: F811
    a, b = schools
    for key, calls in GATED.items():
        rows = _ok(_patch(client, "ABC", {key: False}, note="Not in their plan"))
        row = next(r for r in rows if r["key"] == key)
        assert row == {**row, "enabled": False, "source": "override", "note": "Not in their plan",
                       "updated_by": "owner@example.com"}
        for username, password, paths in calls:
            tb = _token(client, "ABC", username, password, b.admin)
            ta = _token(client, "DEFAULT", username, password, a.admin)
            assert _ok(client.get("/auth/me", headers=bearer(tb)))["features"][key] is False
            for path in paths:
                r = client.get(path, headers=bearer(tb))
                assert r.status_code == 403, f"{key} {username} {path}: {r.status_code}"
                assert r.json()["code"] == "feature_disabled" and r.json()["feature"] == key
                assert FEATURES[key].label in r.json()["detail"]
                # the other school is untouched
                assert client.get(path, headers=bearer(ta)).status_code == 200, f"A {path}"
        # back to the default: everything as before
        _ok(_patch(client, "ABC", {key: None}))
        for username, password, paths in calls:
            tb = _token(client, "ABC", username, password, b.admin)
            for path in paths:
                assert client.get(path, headers=bearer(tb)).status_code == 200, f"{key} back on {path}"


def test_side_effects_follow_the_flags(client, schools):  # noqa: F811
    _, b = schools
    student = _ok(login_to(client, "ABC", "student1", "studpass"))
    before = _ok(client.get("/student/dashboard", headers=bearer(student)))
    assert before["pending_fees"]["amount_due"] > 0
    rows = {r["code"]: r for r in _ok(client.get("/platform/organisations", headers=PLATFORM))}
    assert "fee_claims_waiting" in rows["ABC"]["alerts"] and rows["ABC"]["features_off"] == []

    _ok(_patch(client, "ABC", {"fees": False, "transport": False, "attendance": False}))
    dash = _ok(client.get("/student/dashboard", headers=bearer(student)))
    assert dash["pending_fees"] == {"count": 0, "amount_due": 0.0}
    assert dash["attendance"]["percent"] is None
    sections = _ok(client.get("/updates", headers=bearer(student)))["sections"]
    assert not {"fees", "transport", "attendance"} & set(sections) and "notices" in sections
    admin_sections = _ok(client.get("/updates", headers=bearer(b.admin)))["sections"]
    assert "fee_claims" not in admin_sections
    rows = {r["code"]: r for r in _ok(client.get("/platform/organisations", headers=PLATFORM))}
    assert rows["ABC"]["features_off"] == ["attendance", "fees", "transport"]
    assert not {"fee_claims_waiting", "fees_overdue", "attendance_not_marked"} & set(rows["ABC"]["alerts"])
    assert rows["DEFAULT"]["features_off"] == [] and "fee_claims_waiting" in rows["DEFAULT"]["alerts"]

    # nothing was deleted: back on, the numbers are the same
    _ok(_patch(client, "ABC", {"fees": True, "transport": None, "attendance": None}))
    again = _ok(client.get("/student/dashboard", headers=bearer(student)))
    assert again["pending_fees"] == before["pending_fees"] and again["attendance"] == before["attendance"]


def test_platform_rules_and_audit(client, schools):  # noqa: F811
    assert _patch(client, "ABC", {"nope": False}).status_code == 400
    assert _patch(client, "ZZZ", {"fees": False}).status_code == 404
    assert _patch(client, "ABC", {}).status_code == 422
    assert client.patch("/platform/organisations/ABC/features", json={"changes": {"fees": False}}).status_code == 403
    _ok(_patch(client, "ABC", {"salary": False}, note="trial"))
    entry = _ok(client.get("/platform/activity?org=ABC&action=organisation.features", headers=PLATFORM))
    items = entry["items"] if isinstance(entry, dict) else entry
    assert items and items[0]["action"] == "organisation.features_update"
    assert items[0]["details"]["changes"] == {"salary": [True, False]}
    assert items[0]["details"]["operator"] == "owner@example.com"
