"""Brute-force protection for password checks.

Failed attempts are stored in the database (so the limits hold across all
worker processes) and counted over a sliding window (LOGIN_WINDOW_MINUTES).

Apps that can show a CAPTCHA (they send `captcha_supported: true` with the
login, plus their install id as X-Device-Id):

* per account            – after LOGIN_CAPTCHA_AFTER_ACCOUNT_FAILURES (3) a
                           CAPTCHA is needed; at LOGIN_MAX_FAILURES_PER_ACCOUNT
                           (10) the account is locked for the window
* per account + device   – LOGIN_MAX_FAILURES_PER_ACCOUNT_DEVICE (5) locks that
                           device out of that account (without a device id the
                           IP address stands in for the device)
* per address            – after LOGIN_CAPTCHA_AFTER_IP_FAILURES (20) every
                           sign-in from that address needs a CAPTCHA; only a
                           flood (LOGIN_IP_FLOOD_LIMIT, 500) blocks it

So a shared address (mobile carriers, school Wi-Fi) is never a reason on its
own to refuse someone with the right password.

Apps that can't show a CAPTCHA (builds released before it) keep the original
limits exactly: per account 10, per account + address 5, per address
LOGIN_MAX_FAILURES_PER_IP (50).

A successful login resets the account counters. Blocked callers get HTTP 429
with a Retry-After header; no password is checked while blocked.

Accounts are keyed "<organisation id>:<username>", since usernames are only
unique within a school.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.errors import TooManyRequests
from app.db import Db

RETENTION_DAYS = 30


@dataclass(frozen=True)
class Verdict:
    captcha_required: bool


def account_key(org_id: int, username: str) -> str:
    return f"{org_id}:{username.strip().lower()}"[:100]


def assess(db: Db, username_key: str, ip: str | None, device: str | None,
           captcha_supported: bool = False) -> Verdict:
    """Raise 429 if this attempt must wait; otherwise say whether it needs a CAPTCHA
    (only ever for clients that said they can show one)."""
    now = utcnow()
    window = timedelta(minutes=settings.login_window_minutes)
    window_start = now - window

    last_success = db.scalar(
        "SELECT MAX(created_at) FROM auth_login_attempts "
        "WHERE username = %s AND success = 1 AND created_at >= %s",
        (username_key, window_start),
    )
    # failures before the last successful login don't count against the account
    account_since = max(window_start, last_success) if last_success else window_start
    # the same device (or, for clients that send no device id, the same address)
    pair = "device_id <=> %(dev)s" if device else "device_id IS NULL AND ip <=> %(ip)s"

    row = db.one(
        "SELECT "
        " COALESCE(SUM(username = %(u)s AND created_at > %(since)s), 0) AS account_failures, "
        " MIN(CASE WHEN username = %(u)s AND created_at > %(since)s THEN created_at END) AS account_oldest, "
        f" COALESCE(SUM(username = %(u)s AND {pair} AND created_at > %(since)s), 0) AS pair_failures, "
        f" MIN(CASE WHEN username = %(u)s AND {pair} AND created_at > %(since)s "
        "     THEN created_at END) AS pair_oldest, "
        " COALESCE(SUM(ip <=> %(ip)s), 0) AS ip_failures, "
        " MIN(CASE WHEN ip <=> %(ip)s THEN created_at END) AS ip_oldest "
        "FROM auth_login_attempts "
        "WHERE success = 0 AND created_at >= %(start)s AND (username = %(u)s OR ip <=> %(ip)s)",
        {"u": username_key, "ip": ip, "dev": device, "since": account_since, "start": window_start},
    )

    ip_limit = settings.login_ip_flood_limit if captcha_supported else settings.login_max_failures_per_ip
    limits = (
        (row["account_failures"], settings.login_max_failures_per_account, row["account_oldest"]),
        (row["pair_failures"], settings.login_max_failures_per_account_device, row["pair_oldest"]),
        (row["ip_failures"], ip_limit, row["ip_oldest"]),
    )
    waits = [_wait_seconds(oldest, window, now) for count, limit, oldest in limits if int(count) >= limit]
    if waits:
        retry_after = max(waits)
        minutes = max(1, math.ceil(retry_after / 60))
        raise TooManyRequests(
            f"Too many failed login attempts. Try again in {minutes} minute{'s' if minutes > 1 else ''}.",
            retry_after_seconds=retry_after,
        )
    return Verdict(captcha_required=captcha_supported and (
        int(row["account_failures"]) >= settings.login_captcha_after_account_failures
        or int(row["ip_failures"]) >= settings.login_captcha_after_ip_failures
    ))


def ensure_allowed(db: Db, username_key: str, ip: str | None, device: str | None = None) -> None:
    """For password checks that can't show a CAPTCHA (change-password): only the locks apply."""
    assess(db, username_key, ip, device)


def record(db: Db, username_key: str, ip: str | None, *, success: bool, device: str | None = None) -> None:
    now = utcnow()
    db.execute(
        "INSERT INTO auth_login_attempts (username, ip, device_id, success, created_at) VALUES (%s, %s, %s, %s, %s)",
        (username_key, ip, device, int(success), now),
    )
    db.execute(
        "DELETE FROM auth_login_attempts WHERE created_at < %s LIMIT 1000",
        (now - timedelta(days=RETENTION_DAYS),),
    )


def _wait_seconds(oldest: datetime | None, window: timedelta, now: datetime) -> int:
    if oldest is None:
        return int(window.total_seconds())
    return max(1, int((oldest + window - now).total_seconds()))
