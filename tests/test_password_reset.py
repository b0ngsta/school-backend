"""Forgotten passwords: emailed 6-digit codes, limits, and the email layer."""
from __future__ import annotations

import dataclasses
import re

import pytest
from helpers import bearer, client_from, login

from app.core.config import settings
from app.mail import set_mailer
from app.mail.base import OutgoingEmail, to_mime
from app.mail.memory import MemoryMailer
from app.mail.smtp import SmtpMailer, sender_address
from app.services import email as email_service
from app.services import password_reset

CODE_RE = re.compile(r"\b(\d{6})\b")


@pytest.fixture
def outbox():
    mailer = MemoryMailer()
    set_mailer(mailer)
    yield mailer
    set_mailer(None)


def code_from(mail: OutgoingEmail) -> str:
    return CODE_RE.search(mail.text).group(1)


def set_email(client, admin, user_id: int, address: str) -> None:
    r = client.put(f"/users/{user_id}", json={"email": address}, headers=bearer(admin))
    assert r.status_code == 200, r.text


def test_reset_with_emailed_code_end_to_end(client, school, outbox, db):
    set_email(client, school.admin, school.t1_id, "teacher1@example.com")
    old_session = login(client, "teacher1", "teachpass")

    r = client.post("/auth/forgot-password", json={"login": "teacher1"})
    assert r.status_code == 200
    assert r.json() == {"requested": True, "code_expires_in": settings.reset_code_ttl_minutes * 60}
    [mail] = outbox.to("teacher1@example.com")
    code = code_from(mail)
    assert code not in mail.subject  # codes stay off lock screens
    assert code in mail.html

    assert client.post("/auth/verify-reset-code", json={"login": "teacher1", "code": code}).json() == {"valid": True}
    r = client.post("/auth/reset-password", json={"login": "teacher1", "code": code, "new_password": "brand-new-pw"})
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["user_id"] == school.t1_id and tokens["refresh_token"]
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 200

    # every other device is signed out, the old password is gone, the new one works
    assert client.get("/auth/me", headers=bearer(old_session)).status_code == 401
    assert client.post("/auth/login", json={"username": "teacher1", "password": "teachpass"}).status_code == 401
    assert login(client, "teacher1", "brand-new-pw")["must_change_password"] is False

    # a code works once
    again = client.post("/auth/reset-password", json={"login": "teacher1", "code": code, "new_password": "other-pw-1"})
    assert again.status_code == 400 and again.json() == {"detail": "Invalid or expired code"}

    assert outbox.to("teacher1@example.com")[-1].subject.endswith("password was changed")
    assert db.scalar("SELECT COUNT(*) FROM audit_logs WHERE action = 'auth.password_reset' AND entity_id = %s",
                     (str(school.t1_id),)) == 1


def test_reply_does_not_reveal_whether_an_account_exists(client, school, outbox):
    known_without_email = client.post("/auth/forgot-password", json={"login": "teacher2"})
    unknown = client.post("/auth/forgot-password", json={"login": "nobody-here"})
    unknown_email = client.post("/auth/forgot-password", json={"login": "nobody@example.com"})
    assert known_without_email.json() == unknown.json() == unknown_email.json()
    assert known_without_email.status_code == unknown.status_code == 200
    assert outbox.outbox == []


def test_email_login_and_siblings_sharing_one_address(client, school, outbox):
    a = bearer(school.admin)
    for sid in (school.s1, school.s2):
        r = client.put(f"/students/{sid}", json={"full_name": f"Student {sid - school.s1 + 1}",
                                                  "email": "parent@example.com"}, headers=a)
        assert r.status_code == 200, r.text
    r = client.put(f"/students/{school.s3}", json={"full_name": "Student 3", "email": "Solo.Parent@Example.com"},
                   headers=a)
    assert r.status_code == 200

    # shared address: the email lists the accounts instead of sending a code
    client.post("/auth/forgot-password", json={"login": "PARENT@example.com"})
    [listing] = outbox.to("parent@example.com")
    assert "student1 (Student 1)" in listing.text and "student2 (Student 2)" in listing.text
    assert not CODE_RE.search(listing.text)

    # resetting with the username works for each sibling
    client.post("/auth/forgot-password", json={"login": "student2"})
    code = code_from(outbox.to("parent@example.com")[-1])
    bad = client.post("/auth/reset-password", json={"login": "parent@example.com", "code": code,
                                                    "new_password": "kid2-new-pw"})
    assert bad.status_code == 400  # the shared address does not identify one account
    ok = client.post("/auth/reset-password", json={"login": "student2", "code": code, "new_password": "kid2-new-pw"})
    assert ok.status_code == 200

    # an address used by one account works as the login, case-insensitively
    client.post("/auth/forgot-password", json={"login": "solo.parent@example.com"})
    code = code_from(outbox.to("Solo.Parent@Example.com")[-1])
    ok = client.post("/auth/reset-password", json={"login": "SOLO.PARENT@example.com", "code": code,
                                                   "new_password": "kid3-new-pw"})
    assert ok.status_code == 200 and ok.json()["user_id"] == school.s3


def test_wrong_guesses_burn_the_code_and_are_throttled(client, school, outbox):
    set_email(client, school.admin, school.t1_id, "t1@example.com")
    client.post("/auth/forgot-password", json={"login": "teacher1"})
    code = code_from(outbox.outbox[-1])
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.reset_code_max_attempts):
        r = client.post("/auth/verify-reset-code", json={"login": "teacher1", "code": wrong})
        assert r.status_code == 400
    # the code is now used up, and further guesses from here are throttled
    r = client.post("/auth/reset-password", json={"login": "teacher1", "code": code, "new_password": "whatever1"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    other_ip = client_from("10.9.9.9")
    r = other_ip.post("/auth/reset-password", json={"login": "teacher1", "code": code, "new_password": "whatever1"})
    assert r.status_code == 400  # burnt, even from another address


def test_only_the_newest_code_works_and_codes_expire(client, school, outbox, db):
    set_email(client, school.admin, school.t1_id, "t1@example.com")
    client.post("/auth/forgot-password", json={"login": "teacher1"})
    first = code_from(outbox.outbox[-1])
    client.post("/auth/forgot-password", json={"login": "teacher1"})
    second = code_from(outbox.outbox[-1])
    if first != second:
        assert client.post("/auth/verify-reset-code", json={"login": "teacher1", "code": first}).status_code == 400
    assert client.post("/auth/verify-reset-code", json={"login": "teacher1", "code": second}).status_code == 200

    db.execute("UPDATE password_reset_codes SET expires_at = '2020-01-01' WHERE user_id = %s", (school.t1_id,))
    assert client.post("/auth/verify-reset-code", json={"login": "teacher1", "code": second}).status_code == 400


def test_wrong_codes_per_account_are_capped_per_day(client, school, outbox, db):
    set_email(client, school.admin, school.t1_id, "t1@example.com")
    client.post("/auth/forgot-password", json={"login": "teacher1"})
    code = code_from(outbox.outbox[-1])
    db.execute("UPDATE password_reset_codes SET attempts = 0, created_at = created_at - INTERVAL 1 HOUR "
               "WHERE user_id = %s", (school.t1_id,))
    db.execute("INSERT INTO password_reset_codes (user_id, code_hash, created_at, expires_at, attempts, used_at) "
               "VALUES (%s, 'x', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), %s, UTC_TIMESTAMP(6))",
               (school.t1_id, settings.reset_max_wrong_codes_per_day))
    r = client.post("/auth/verify-reset-code", json={"login": "teacher1", "code": code})
    assert r.status_code == 400  # even the right code, until tomorrow


def test_malformed_codes_count_as_failures(client, school, outbox):
    for code in ("12345", "abcdef", "1234567"):
        assert client.post("/auth/verify-reset-code", json={"login": "admin", "code": code}).status_code == 400


def test_request_limits_apply_to_known_and_unknown_logins_alike(client, school, outbox, monkeypatch):
    for login_name in ("teacher1", "no-such-user"):
        for _ in range(settings.reset_max_codes_per_account):
            assert client.post("/auth/forgot-password", json={"login": login_name}).status_code == 200
        r = client.post("/auth/forgot-password", json={"login": login_name})
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0

    monkeypatch.setattr(password_reset, "settings", dataclasses.replace(settings, reset_max_requests_per_ip=3))
    busy = client_from("10.1.1.1")
    for n in range(3):
        assert busy.post("/auth/forgot-password", json={"login": f"someone{n}"}).status_code == 200
    assert busy.post("/auth/forgot-password", json={"login": "someone-else"}).status_code == 429
    assert client_from("10.1.1.2").post("/auth/forgot-password", json={"login": "someone-else"}).status_code == 200


def test_codes_per_account_are_capped_silently(client, school, outbox):
    set_email(client, school.admin, school.t1_id, "t1@example.com")
    logins = ["teacher1", "t1@example.com"] * settings.reset_max_codes_per_account
    replies = {client.post("/auth/forgot-password", json={"login": name}).status_code for name in logins}
    assert replies == {200}
    assert len(outbox.to("t1@example.com")) == settings.reset_max_codes_per_account


def test_disabled_accounts_get_no_code(client, school, outbox, db):
    set_email(client, school.admin, school.t1_id, "t1@example.com")
    db.execute("UPDATE users SET is_active = 0 WHERE id = %s", (school.t1_id,))
    assert client.post("/auth/forgot-password", json={"login": "teacher1"}).status_code == 200
    assert outbox.outbox == []


def test_reset_lifts_a_login_lockout(client, school, outbox):
    set_email(client, school.admin, school.t1_id, "t1@example.com")
    for _ in range(settings.login_max_failures_per_account):
        client.post("/auth/login", json={"username": "teacher1", "password": "wrong-guess"})
    assert client.post("/auth/login", json={"username": "teacher1", "password": "teachpass"}).status_code == 429

    client.post("/auth/forgot-password", json={"login": "teacher1"})
    code = code_from(outbox.outbox[-1])
    r = client.post("/auth/reset-password", json={"login": "teacher1", "code": code, "new_password": "fresh-start"})
    assert r.status_code == 200
    assert client.post("/auth/login", json={"username": "teacher1", "password": "fresh-start"}).status_code == 200


def test_mail_failures_never_break_the_request(client, school, outbox, caplog):
    set_email(client, school.admin, school.t1_id, "t1@example.com")
    outbox.fail_next = 1
    assert client.post("/auth/forgot-password", json={"login": "teacher1"}).status_code == 200
    assert outbox.outbox == [] and "Email delivery failed" in caplog.text


def test_background_delivery_retries(outbox):
    outbox.fail_next = 2
    message = OutgoingEmail(to="a@example.com", subject="Hi", text="Hello")
    email_service._deliver([message], delays=(0, 0))
    assert outbox.outbox == [message]


# ------------------------------------------------------------ email layer

def test_mime_message_and_sender():
    mime = to_mime(OutgoingEmail(to="a@example.com", subject="Code", text="123", html="<b>123</b>"),
                   "School <no-reply@school.in>")
    assert mime["From"] == "School <no-reply@school.in>" and mime["Message-ID"].endswith("@school.in>")
    assert mime.is_multipart()
    with pytest.raises(ValueError):
        to_mime(OutgoingEmail(to="a@example.com\r\nBcc: x@evil.test", subject="x", text="x"), "s@school.in")

    named = dataclasses.replace(settings, email_from="", smtp_username="mailer@school.in", school_name="Green Valley")
    assert sender_address(named) == "Green Valley <mailer@school.in>"


def test_smtp_mailer_uses_tls_login_and_one_connection(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def starttls(self, context):
            calls.append(("starttls", context.verify_mode.name))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, mime):
            calls.append(("send", mime["To"]))

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append(("quit",))

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    mailer = SmtpMailer(host="smtp.example.com", port=587, username="u", password="p", security="starttls",
                        timeout=5, sender="School <s@example.com>")
    mailer.send([OutgoingEmail(to=f"{n}@example.com", subject="s", text="t") for n in (1, 2)])
    assert calls == [("connect", "smtp.example.com", 587), ("starttls", "CERT_REQUIRED"), ("login", "u"),
                     ("send", "1@example.com"), ("send", "2@example.com"), ("quit",)]


# ----------------------------------------------- emails on the existing forms

def test_forms_check_email_format_but_still_allow_blank(client, school, monkeypatch):
    a = bearer(school.admin)
    base = {"password": "secret1", "full_name": "New Coordinator", "user_type": "coordinator"}
    bad = client.post("/users", json={**base, "username": "coord1", "email": "not-an-email"}, headers=a)
    assert bad.status_code == 422 and "valid email" in bad.text
    assert client.post("/users", json={**base, "username": "coord2", "email": ""}, headers=a).status_code == 201
    assert client.post("/users", json={**base, "username": "coord3"}, headers=a).status_code == 201

    from app.models import students, users
    strict = dataclasses.replace(settings, require_user_email=True)
    monkeypatch.setattr(users, "settings", strict)
    monkeypatch.setattr(students, "settings", strict)
    r = client.post("/users", json={**base, "username": "coord4"}, headers=a)
    assert r.status_code == 422 and "Email is required" in r.text
    assert client.post("/users", json={**base, "username": "coord5", "email": "c5@example.com"},
                       headers=a).status_code == 201
    student = {"full_name": "Student 1"}
    assert client.put(f"/students/{school.s1}", json={**student, "email": ""}, headers=a).status_code == 422
    assert client.put(f"/students/{school.s1}", json={**student, "phone": "123456"}, headers=a).status_code == 200


def test_staff_edit_endpoint(client, school, db):
    coordinator = login(client, "coordinator")
    r = client.put(f"/users/{school.t1_id}", json={"full_name": "Teacher One", "phone": "99999"},
                   headers=bearer(coordinator))
    assert r.status_code == 200
    row = db.one("SELECT full_name, phone, email FROM users WHERE id = %s", (school.t1_id,))
    assert row == {"full_name": "Teacher One", "phone": "99999", "email": None}

    principal_id = db.scalar("SELECT id FROM users WHERE username = 'principal'")
    assert client.put(f"/users/{principal_id}", json={"email": "p@example.com"},
                      headers=bearer(coordinator)).status_code == 403
    assert client.put(f"/users/{principal_id}", json={"email": "p@example.com"},
                      headers=bearer(school.admin)).status_code == 200
    assert client.put(f"/users/{school.s1}", json={"email": "x@example.com"},
                      headers=bearer(school.admin)).status_code == 400
    assert client.put(f"/users/{school.t1_id}", json={"email": "x"}, headers=bearer(school.admin)).status_code == 422
    assert client.put(f"/users/{school.t1_id}", json={}, headers=bearer(school.t1)).status_code == 403


def test_only_one_principal_through_the_form(client, school):
    r = client.post("/users", json={"username": "vice", "password": "secret1", "full_name": "Vice",
                                    "user_type": "principal"}, headers=bearer(school.admin))
    assert r.status_code == 409
    assert r.json() == {"detail": "There is already a principal (principal). Only one principal is allowed."}


def test_check_data_lists_accounts_that_cannot_reset(db, capsys):
    from app.cli import check_data

    db.execute("UPDATE users SET email = 'a@example.com' WHERE username = 'admin'")
    check_data(db)
    out = capsys.readouterr().out
    assert "Nothing blocks the migrations." in out
    assert "4 active account(s) have no email" in out and "principal (principal)" in out and "admin (admin)" not in out


def test_forgot_password_does_the_same_work_for_any_login(client, school, outbox, monkeypatch):
    """Account lookups happen after the reply, so response time cannot reveal
    whether an account exists."""
    from app.db import session as db_session

    set_email(client, school.admin, school.t1_id, "t1@example.com")
    jobs, statements = [], []
    monkeypatch.setattr(password_reset.background, "submit", lambda fn, *args: jobs.append((fn, args)))
    real_run = db_session.Db._run

    def counting_run(self, op):
        statements.append(op)
        return real_run(self, op)

    monkeypatch.setattr(db_session.Db, "_run", counting_run)
    counts = []
    for name in ("teacher1", "t1@example.com", "no-such-user"):
        statements.clear()
        assert client.post("/auth/forgot-password", json={"login": name}).status_code == 200
        counts.append(len(statements))
    assert len(set(counts)) == 1 and len(jobs) == 3 and outbox.outbox == []

    for fn, args in jobs:  # the queued jobs look the accounts up and send the codes afterwards
        fn(*args)
    assert len(outbox.to("t1@example.com")) == 2 and len(outbox.outbox) == 2
