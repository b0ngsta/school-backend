"""Forgot password: a 6-digit code is emailed to the account's address and
exchanged for a new password.

    POST /auth/forgot-password    {login}                      -> code emailed
    POST /auth/verify-reset-code  {login, code}                -> optional check
    POST /auth/reset-password     {login, code, new_password}  -> new tokens

`login` is a username or an email address. The reply to forgot-password is
the same whether or not an account matches, and is sent before the account
is even looked up (that happens in a background job), so neither the reply
nor its timing tells anyone who has an account. If several accounts share
one email address (siblings with a parent's email), that address receives
the list of their usernames instead of a code, and the user resets with the
username.

Limits
* per address typed and per caller IP: RESET_MAX_CODES_PER_ACCOUNT /
  RESET_MAX_REQUESTS_PER_IP requests per RESET_WINDOW_MINUTES (HTTP 429);
  counted for every request, matching or not, so a 429 reveals nothing;
* per account: at most RESET_MAX_CODES_PER_ACCOUNT codes per window, whatever
  was typed (extra requests are silently ignored);
* a code lasts RESET_CODE_TTL_MINUTES, allows RESET_CODE_MAX_ATTEMPTS wrong
  guesses, and only the newest one works;
* wrong codes are throttled like failed logins (per username/email typed,
  per caller), under their own counter so that a login lockout never blocks
  the reset that fixes it;
* at most RESET_MAX_WRONG_CODES_PER_DAY wrong codes per account per day,
  across all its codes; beyond that every code is refused until the next
  day (the same "invalid code" reply, so it reveals nothing).

A successful reset signs the account out everywhere, clears
must_change_password, is audited, and the account is told by email.

Every request names the school (`org_code`, DEFAULT_ORG_CODE when left
out): usernames and emails are only looked up inside that school, the rate
limits count per school + login, and emails carry the school's name.
"""
from __future__ import annotations

import html
import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar

from app.core import security
from app.core.clock import SCHOOL_TZ, utcnow
from app.core.config import settings
from app.core.context import client_ip
from app.core.errors import BadRequest, TooManyRequests
from app.db import Db, pool
from app.mail import OutgoingEmail
from app.models.auth import TokenResponse
from app.services import audit, auth, background, email, login_throttle, organisations, sessions

log = logging.getLogger(__name__)

INVALID_CODE = "Invalid or expired code"
RETENTION_DAYS = 30
MAX_LISTED_ACCOUNTS = 20

T = TypeVar("T")

_ACCOUNT_SELECT = (
    "SELECT u.id, u.org_id, u.username, u.full_name, u.email, u.user_type, u.is_active, o.name AS org_name "
    "FROM users u JOIN organisations o ON o.id = u.org_id "
)


def _login(login: str) -> str:
    return login.strip().lower()[:110]


def _login_key(org_id: int, login: str) -> str:
    """Rate-limit key: the school and the username/email typed."""
    return f"{org_id}:{_login(login)}"[:120]


def _guess_key(key: str) -> str:
    """Throttle key for wrong codes, separate from the login counters."""
    return f"reset:{key}"[:100]


# ----------------------------------------------------------------- request

def request_code(db: Db, login: str, org_code: str | None = None) -> int:
    """Email a reset code if `login` matches an active account of the school
    with an email address. Returns the code lifetime in seconds (always the same)."""
    org = organisations.resolve(db, org_code)
    key, ip, now = _login_key(org["id"], login), client_ip(), utcnow()
    _ensure_request_allowed(db, key, ip, now)
    with db.transaction():
        db.execute("INSERT INTO auth_reset_requests (login, ip, created_at) VALUES (%s, %s, %s)", (key, ip, now))
        db.execute("DELETE FROM auth_reset_requests WHERE created_at < %s LIMIT 1000",
                   (now - timedelta(days=RETENTION_DAYS),))
    # Everything that depends on the account happens after the reply, so the
    # response time is the same whether or not the account exists.
    background.submit(_handle_request, org["id"], _login(login), ip, now)
    return settings.reset_code_ttl_minutes * 60


@background.task
def _handle_request(org_id: int, login: str, ip: str | None, now: datetime) -> None:
    db = Db(pool)
    try:
        db.bind_org(org_id)
        with db.transaction():
            db.execute("DELETE FROM password_reset_codes WHERE created_at < %s LIMIT 1000",
                       (now - timedelta(days=RETENTION_DAYS),))
            accounts = _find_accounts(db, org_id, login)
            if len(accounts) > 1:
                email.send_after_commit(db, [_several_accounts_email(login, accounts)])
            elif accounts and accounts[0]["is_active"] and accounts[0]["email"]:
                _issue_code(db, accounts[0], ip, now)
            else:
                log.info("Password reset requested for an unknown, inactive or email-less account")
    finally:
        db.close()


def _ensure_request_allowed(db: Db, key: str, ip: str | None, now: datetime) -> None:
    window = timedelta(minutes=settings.reset_window_minutes)
    row = db.one(
        "SELECT "
        " COALESCE(SUM(login = %(k)s), 0) AS by_login, "
        " MIN(CASE WHEN login = %(k)s THEN created_at END) AS login_oldest, "
        " COALESCE(SUM(ip <=> %(ip)s), 0) AS by_ip, "
        " MIN(CASE WHEN ip <=> %(ip)s THEN created_at END) AS ip_oldest "
        "FROM auth_reset_requests WHERE created_at >= %(start)s AND (login = %(k)s OR ip <=> %(ip)s)",
        {"k": key, "ip": ip, "start": now - window},
    )
    limits = (
        (row["by_login"], settings.reset_max_codes_per_account, row["login_oldest"]),
        (row["by_ip"], settings.reset_max_requests_per_ip, row["ip_oldest"]),
    )
    waits = [max(1, int((oldest + window - now).total_seconds())) if oldest else int(window.total_seconds())
             for count, limit, oldest in limits if int(count) >= limit]
    if waits:
        minutes = max(1, math.ceil(max(waits) / 60))
        raise TooManyRequests(
            f"Too many reset requests. Try again in {minutes} minute{'s' if minutes > 1 else ''}.",
            retry_after_seconds=max(waits),
        )


def _find_accounts(db: Db, org_id: int, login: str) -> list[dict]:
    """The school's account named by a username, else its active accounts
    using that email address (comparison is case-insensitive, like logins)."""
    if not login:
        return []
    user = db.one(_ACCOUNT_SELECT + "WHERE u.org_id = %s AND u.username = %s", (org_id, login))
    if user:
        return [user]
    if "@" not in login:
        return []
    return db.all(
        _ACCOUNT_SELECT + "WHERE u.org_id = %s AND u.email = %s AND u.is_active = 1 ORDER BY u.id LIMIT %s",
        (org_id, login, MAX_LISTED_ACCOUNTS),
    )


def _issue_code(db: Db, user: dict, ip: str | None, now: datetime) -> None:
    recent = db.scalar(
        "SELECT COUNT(*) FROM password_reset_codes WHERE user_id = %s AND created_at >= %s",
        (user["id"], now - timedelta(minutes=settings.reset_window_minutes)),
    )
    if int(recent or 0) >= settings.reset_max_codes_per_account:
        log.info("Reset code limit reached for user %s; request ignored", user["id"])
        return
    # only the newest code is valid
    db.execute("UPDATE password_reset_codes SET used_at = %s WHERE user_id = %s AND used_at IS NULL",
               (now, user["id"]))
    code = security.new_reset_code()
    db.execute(
        "INSERT INTO password_reset_codes (user_id, code_hash, created_at, expires_at, ip) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user["id"], security.reset_code_digest(user["id"], code), now,
         now + timedelta(minutes=settings.reset_code_ttl_minutes), ip),
    )
    email.send_after_commit(db, [_code_email(user, code)])


# ------------------------------------------------------------------ redeem

def verify_code(db: Db, login: str, code: str, org_code: str | None = None) -> None:
    """Check a code without using it up (wrong guesses still count)."""
    _redeem(db, login, code, org_code, then=None)


def reset_password(db: Db, login: str, code: str, new_password: str, org_code: str | None = None) -> TokenResponse:
    def finish(user: dict) -> TokenResponse:
        auth.set_password(db, user["id"], new_password, reason="password_reset")
        audit.record(db, user["id"], "auth.password_reset", "user", user["id"])
        session = sessions.open_session(db, user["id"])
        fresh = db.one(sessions.TOKEN_USER_SQL, (user["id"],))
        email.send_after_commit(db, [_password_changed_email(user)])
        return auth.token_response(fresh, session)

    return _redeem(db, login, code, org_code, then=finish)


def _redeem(db: Db, login: str, code: str, org_code: str | None, then: Callable[[dict], T] | None) -> T | None:
    """Validate the code; with `then`, mark it used and run `then(user)` in
    the same transaction. Wrong guesses are committed before the error."""
    org = organisations.resolve(db, org_code)
    key, ip = _login_key(org["id"], login), client_ip()
    login_throttle.ensure_allowed(db, _guess_key(key), ip)
    code = (code or "").strip()

    result: T | None = None
    valid = False
    with db.transaction():
        accounts = _find_accounts(db, org["id"], _login(login)) if _well_formed(code) else []
        user = accounts[0] if len(accounts) == 1 and accounts[0]["is_active"] else None
        # the lookup runs for unknown accounts too (user id 0 matches nothing),
        # so timing does not tell them apart
        row = db.one(
            "SELECT id, code_hash, expires_at, attempts FROM password_reset_codes "
            "WHERE user_id = %s AND used_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE",
            (user["id"] if user else 0,),
        )
        now = utcnow()
        live = (
            row is not None
            and now < row["expires_at"]
            and row["attempts"] < settings.reset_code_max_attempts
            and _wrong_codes_today(db, user["id"], now) < settings.reset_max_wrong_codes_per_day
        )
        if live and security.reset_code_matches(user["id"], code, row["code_hash"]):
            valid = True
            if then is not None:
                db.execute("UPDATE password_reset_codes SET used_at = %s WHERE id = %s", (now, row["id"]))
                # earlier wrong passwords must not keep the account locked out
                login_throttle.record(db, login_throttle.account_key(org["id"], user["username"]), ip, success=True)
                result = then(user)
        else:
            if live:
                attempts = row["attempts"] + 1
                burnt = attempts >= settings.reset_code_max_attempts
                db.execute("UPDATE password_reset_codes SET attempts = %s, used_at = %s WHERE id = %s",
                           (attempts, now if burnt else None, row["id"]))
            login_throttle.record(db, _guess_key(key), ip, success=False)

    if not valid:
        raise BadRequest(INVALID_CODE)
    return result


def _wrong_codes_today(db: Db, user_id: int, now: datetime) -> int:
    return int(db.scalar(
        "SELECT COALESCE(SUM(attempts), 0) FROM password_reset_codes WHERE user_id = %s AND created_at >= %s",
        (user_id, now - timedelta(days=1)),
    ) or 0)


def _well_formed(code: str) -> bool:
    return len(code) == security.RESET_CODE_DIGITS and code.isdigit()


# ------------------------------------------------------------------ emails

def _greeting(user: dict) -> str:
    return f"Hello {user['full_name']},"


def _html(paragraphs: list[str]) -> str:
    return "".join(f"<p>{p}</p>" for p in paragraphs)


def _school(user: dict) -> str:
    return user.get("org_name") or settings.school_name


def _code_email(user: dict, code: str) -> OutgoingEmail:
    school, minutes = _school(user), settings.reset_code_ttl_minutes
    lines = [
        _greeting(user),
        f"Use this code to reset the password of the {school} account '{user['username']}':",
        code,
        f"The code expires in {minutes} minutes. If you did not ask for it, ignore this email; "
        "your password stays the same.",
    ]
    e = html.escape
    return OutgoingEmail(
        to=user["email"],
        subject=f"Your {school} password reset code",
        text="\n\n".join(lines) + f"\n\n— {school}\n",
        html=_html([e(lines[0]), e(lines[1]),
                    f'<strong style="font-size:28px;letter-spacing:6px">{code}</strong>',
                    e(lines[3]), f"— {e(school)}"]),
    )


def _several_accounts_email(address: str, accounts: list[dict]) -> OutgoingEmail:
    school = _school(accounts[0])
    names = [f"{a['username']} ({a['full_name']})" for a in accounts]
    intro = f"This email address is linked to several {school} accounts:"
    outro = ("To reset a password, open the app, choose 'Forgot password' and enter the username of "
             "the account. If you did not ask for this, ignore this email.")
    e = html.escape
    return OutgoingEmail(
        to=accounts[0]["email"] or address,
        subject=f"Your {school} accounts",
        text=f"{intro}\n\n" + "\n".join(f"  - {n}" for n in names) + f"\n\n{outro}\n\n— {school}\n",
        html=_html([e(intro), "<br>".join(e(n) for n in names), e(outro), f"— {e(school)}"]),
    )


def _password_changed_email(user: dict) -> OutgoingEmail:
    school = _school(user)
    when = datetime.now(SCHOOL_TZ).strftime("%d %b %Y, %I:%M %p")
    lines = [
        _greeting(user),
        f"The password of the {school} account '{user['username']}' was reset on {when}. "
        "All devices were signed out.",
        "If this was not you, contact the school office straight away.",
    ]
    e = html.escape
    return OutgoingEmail(
        to=user["email"],
        subject=f"Your {school} password was changed",
        text="\n\n".join(lines) + f"\n\n— {school}\n",
        html=_html([*(e(line) for line in lines), f"— {e(school)}"]),
    )
