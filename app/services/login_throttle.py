"""Brute-force protection for password checks.

Failed attempts are stored in the database (so the limits hold across all
worker processes) and counted over a sliding window:

* per account            – LOGIN_MAX_FAILURES_PER_ACCOUNT    (default 10)
* per account + address  – LOGIN_MAX_FAILURES_PER_ACCOUNT_IP (default 5)
* per address            – LOGIN_MAX_FAILURES_PER_IP         (default 50)

A successful login resets the account counters. Blocked callers get HTTP 429
with a Retry-After header; no password is checked while blocked.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.core.errors import TooManyRequests
from app.db import Db

RETENTION_DAYS = 30


def account_key(username: str) -> str:
    return username.strip().lower()[:100]


def ensure_allowed(db: Db, username_key: str, ip: str | None) -> None:
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

    row = db.one(
        "SELECT "
        " COALESCE(SUM(username = %(u)s AND created_at > %(since)s), 0) AS account_failures, "
        " MIN(CASE WHEN username = %(u)s AND created_at > %(since)s THEN created_at END) AS account_oldest, "
        " COALESCE(SUM(username = %(u)s AND ip <=> %(ip)s AND created_at > %(since)s), 0) AS pair_failures, "
        " MIN(CASE WHEN username = %(u)s AND ip <=> %(ip)s AND created_at > %(since)s "
        "     THEN created_at END) AS pair_oldest, "
        " COALESCE(SUM(ip <=> %(ip)s), 0) AS ip_failures, "
        " MIN(CASE WHEN ip <=> %(ip)s THEN created_at END) AS ip_oldest "
        "FROM auth_login_attempts "
        "WHERE success = 0 AND created_at >= %(start)s AND (username = %(u)s OR ip <=> %(ip)s)",
        {"u": username_key, "ip": ip, "since": account_since, "start": window_start},
    )

    limits = (
        (row["account_failures"], settings.login_max_failures_per_account, row["account_oldest"]),
        (row["pair_failures"], settings.login_max_failures_per_account_ip, row["pair_oldest"]),
        (row["ip_failures"], settings.login_max_failures_per_ip, row["ip_oldest"]),
    )
    waits = [_wait_seconds(oldest, window, now) for count, limit, oldest in limits if int(count) >= limit]
    if waits:
        retry_after = max(waits)
        minutes = max(1, math.ceil(retry_after / 60))
        raise TooManyRequests(
            f"Too many failed login attempts. Try again in {minutes} minute{'s' if minutes > 1 else ''}.",
            retry_after_seconds=retry_after,
        )


def record(db: Db, username_key: str, ip: str | None, *, success: bool) -> None:
    now = utcnow()
    db.execute(
        "INSERT INTO auth_login_attempts (username, ip, success, created_at) VALUES (%s, %s, %s, %s)",
        (username_key, ip, int(success), now),
    )
    db.execute(
        "DELETE FROM auth_login_attempts WHERE created_at < %s LIMIT 1000",
        (now - timedelta(days=RETENTION_DAYS),),
    )


def _wait_seconds(oldest: datetime | None, window: timedelta, now: datetime) -> int:
    if oldest is None:
        return int(window.total_seconds())
    return max(1, int((oldest + window - now).total_seconds()))
