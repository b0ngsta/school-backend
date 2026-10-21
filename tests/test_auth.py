"""Authentication: tokens, refresh rotation, revocation, throttling, passwords."""
from dataclasses import replace
from datetime import timedelta

import jwt
import pytest
from helpers import bearer, client_from, login

from app.core import clock, security
from app.services import login_throttle, sessions


def test_login_keeps_original_fields_and_adds_refresh(client):
    body = login(client, "admin")
    for key in ("access_token", "token_type", "user_id", "user_type", "full_name"):
        assert key in body
    assert body["token_type"] == "bearer"
    assert body["refresh_token"].startswith("rt1.")
    assert body["expires_in"] == 15 * 60
    assert body["refresh_expires_in"] > 29 * 86400
    assert body["must_change_password"] is True  # seeded password is public

    me = client.get("/auth/me", headers=bearer(body))
    assert me.status_code == 200
    # the original fields, plus the school the account belongs to
    assert set(me.json()) == {"id", "username", "full_name", "email", "phone", "user_type", "is_active",
                              "organisation"}
    assert me.json()["organisation"] == {"id": 1, "code": "DEFAULT", "name": body["org_name"]}
    assert body["org_code"] == "DEFAULT"


def test_legacy_sha256_hash_is_upgraded_to_argon2(client, db):
    assert db.scalar("SELECT password_hash FROM users WHERE username='admin'").startswith("seedsalt$")
    login(client, "admin")
    assert db.scalar("SELECT password_hash FROM users WHERE username='admin'").startswith("$argon2id$")
    login(client, "admin")  # still works with the upgraded hash


def test_wrong_password_and_unknown_user_look_identical(client):
    wrong = client.post("/auth/login", json={"username": "admin", "password": "nope"})
    unknown = client.post("/auth/login", json={"username": "ghost", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid username or password"}


def test_disabled_account(client, db):
    db.execute("UPDATE users SET is_active = 0 WHERE username = 'driver'")
    r = client.post("/auth/login", json={"username": "driver", "password": "driver123"})
    assert r.status_code == 403 and r.json() == {"detail": "Account disabled"}


def test_brute_force_is_throttled_per_account_and_ip(client):
    attacker = client_from("203.0.113.9")
    for _ in range(5):
        assert attacker.post("/auth/login", json={"username": "admin", "password": "x"}).status_code == 401
    blocked = attacker.post("/auth/login", json={"username": "admin", "password": "admin123"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    # the real admin on another network is not locked out by that
    assert client_from("198.51.100.7").post(
        "/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200


def test_account_lock_after_distributed_attack_and_reset_on_success(client, monkeypatch):
    now = [clock.utcnow()]
    monkeypatch.setattr(login_throttle, "utcnow", lambda: now[0])

    def fail_from(ip):
        return client_from(ip).post("/auth/login", json={"username": "principal", "password": "x"})

    for i in range(9):
        assert fail_from(f"203.0.113.{i}").status_code == 401
    now[0] += timedelta(seconds=2)
    login(client, "principal")  # success resets the account counter
    now[0] += timedelta(seconds=2)
    for i in range(9):
        assert fail_from(f"203.0.114.{i}").status_code == 401
    assert fail_from("203.0.115.1").status_code == 401  # 10th failure since the success
    r = client.post("/auth/login", json={"username": "principal", "password": "principal123"})
    assert r.status_code == 429
    now[0] += timedelta(minutes=16)  # window passed
    login(client, "principal")


def test_refresh_rotates_tokens(client):
    first = login(client, "admin")
    r = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["user_id"] == first["user_id"] and second["user_type"] == "admin"
    assert client.get("/auth/me", headers=bearer(second)).status_code == 200


def test_parallel_refresh_with_same_token_is_safe(client):
    """An app waking up fires several requests that all refresh at once."""
    tokens = login(client, "admin")
    a = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    b = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert a.status_code == b.status_code == 200
    assert a.json()["refresh_token"] == b.json()["refresh_token"]
    # and the session keeps working afterwards
    c = client.post("/auth/refresh", json={"refresh_token": b.json()["refresh_token"]})
    assert c.status_code == 200
    assert client.get("/auth/me", headers=bearer(c.json())).status_code == 200


def test_reused_refresh_token_after_grace_revokes_the_session(client, monkeypatch):
    tokens = login(client, "admin")
    rotated = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()

    later = clock.utcnow() + timedelta(minutes=5)
    monkeypatch.setattr(sessions, "utcnow", lambda: later)

    stolen = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert stolen.status_code == 401
    # the whole session is gone: the legitimate holder must log in again
    assert client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 401
    assert client.get("/auth/me", headers=bearer(rotated)).status_code == 401


def test_app_resumes_after_hours_in_background(client, monkeypatch):
    """Access token long expired; the refresh token still brings the app back."""
    tokens = login(client, "admin")
    expired = jwt.encode(
        {**jwt.decode(tokens["access_token"], options={"verify_signature": False}), "exp": 1, "iat": 0},
        security._ACCESS_KEY, algorithm="HS256",
    )
    r = client.get("/auth/me", headers=bearer(expired))
    assert r.status_code == 401
    assert 'error_description="expired"' in r.headers["WWW-Authenticate"]

    six_hours_later = clock.utcnow() + timedelta(hours=6)
    monkeypatch.setattr(sessions, "utcnow", lambda: six_hours_later)
    refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert client.get("/auth/me", headers=bearer(refreshed.json())).status_code == 200


def test_refresh_token_expires_after_idle_period(client, monkeypatch):
    tokens = login(client, "admin")
    much_later = clock.utcnow() + timedelta(days=31)
    monkeypatch.setattr(sessions, "utcnow", lambda: much_later)
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


@pytest.mark.parametrize("token", [
    "garbage",
    "rt1.00000000000000000000000000000000.1.AAAA",
])
def test_invalid_refresh_tokens(client, token):
    assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401


def test_forged_refresh_mac_is_rejected(client):
    tokens = login(client, "admin")
    prefix, sid, gen, mac = tokens["refresh_token"].split(".")
    forged = f"{prefix}.{sid}.{int(gen) + 1}.{mac}"
    assert client.post("/auth/refresh", json={"refresh_token": forged}).status_code == 401


def test_logout_kills_the_access_token_immediately(client):
    tokens = login(client, "admin")
    assert client.post("/auth/logout", headers=bearer(tokens)).json() == {"logged_out": True}
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_with_only_the_refresh_token(client):
    tokens = login(client, "admin")
    r = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 401


def test_logout_all_signs_out_every_device(client):
    phone, laptop = login(client, "admin"), login(client, "admin")
    assert client.post("/auth/logout-all", headers=bearer(phone)).status_code == 200
    for device in (phone, laptop):
        assert client.get("/auth/me", headers=bearer(device)).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": device["refresh_token"]}).status_code == 401


def test_change_password(client, db):
    phone, laptop = login(client, "admin"), login(client, "admin")
    bad = client.post("/auth/change-password", headers=bearer(phone),
                      json={"current_password": "wrong", "new_password": "N3w-secret!"})
    assert bad.status_code == 400

    r = client.post("/auth/change-password", headers=bearer(phone),
                    json={"current_password": "admin123", "new_password": "N3w-secret!"})
    assert r.status_code == 200
    fresh = r.json()
    assert fresh["must_change_password"] is False
    assert client.get("/auth/me", headers=bearer(fresh)).status_code == 200
    for old in (phone, laptop):
        assert client.get("/auth/me", headers=bearer(old)).status_code == 401
    assert client.post("/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 401
    login(client, "admin", "N3w-secret!")


def test_deactivating_a_user_cuts_access_at_once(client, db):
    tokens = login(client, "driver")
    db.execute("UPDATE users SET is_active = 0 WHERE username = 'driver'")
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_deleted_user_tokens_stop_working(school):
    client, a = school.client, bearer(school.admin)
    assert client.delete(f"/users/{school.t3_id}", headers=a).status_code == 200
    assert client.get("/auth/me", headers=bearer(school.t3)).status_code == 401


@pytest.mark.parametrize("make_token", [
    lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),                      # bad signature
    lambda t: jwt.encode(jwt.decode(t, options={"verify_signature": False}), "guess", algorithm="HS256"),
    lambda t: jwt.encode(jwt.decode(t, options={"verify_signature": False}), None, algorithm="none"),
    lambda t: "eyJ1aWQiOjEsInR5cGUiOiJhZG1pbiIsImV4cCI6OTk5OTk5OTk5OX0.c2ln",           # old token format
])
def test_tampered_tokens_are_rejected(client, make_token):
    tokens = login(client, "admin")
    assert client.get("/auth/me", headers=bearer(make_token(tokens["access_token"]))).status_code == 401


def test_session_limit_signs_out_oldest_device(client, monkeypatch):
    monkeypatch.setattr(sessions, "settings", replace(sessions.settings, max_sessions_per_user=3))
    oldest = login(client, "admin")
    for _ in range(3):
        login(client, "admin")
    assert client.get("/auth/me", headers=bearer(oldest)).status_code == 401


def test_missing_token(client):
    r = client.get("/students")
    assert r.status_code == 401 and r.json() == {"detail": "Missing token"}


def test_enforced_password_change_blocks_everything_else(client, monkeypatch):
    from app import dependencies

    monkeypatch.setattr(dependencies, "settings", replace(dependencies.settings, enforce_password_change=True))
    tokens = login(client, "admin")
    assert client.get("/classes", headers=bearer(tokens)).json() == {"detail": "Password change required"}
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 200
    fresh = client.post("/auth/change-password", headers=bearer(tokens),
                        json={"current_password": "admin123", "new_password": "Better-pass-1"}).json()
    assert client.get("/classes", headers=bearer(fresh)).status_code == 200
