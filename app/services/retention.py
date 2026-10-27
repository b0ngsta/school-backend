"""The daily clean-up: rows that only grow and are no use after a while.

Started once a day by the background worker (services/background.py) in one
process only (table `scheduled_runs`), or by `python -m app.cli cleanup` /
POST /internal/jobs/cleanup. RETENTION_ENABLED=false turns the automatic run off.

| Table | Kept for | Setting |
|---|---|---|
| notifications (the in-app inbox) | 180 days | NOTIFICATIONS_RETENTION_DAYS |
| audit_logs | 730 days (the DPDP Rules ask for at least a year of logs) | AUDIT_RETENTION_DAYS (min 365) |
| background_jobs that finished | 14 days (failed ones 90) | JOBS_RETENTION_DAYS |
| auth_sessions (long expired; HQ shows "last seen" from them) | 365 days after last use | – |
| auth_login_attempts, auth_reset_requests, password_reset_codes | 30 days (as before) | – |
| auth_captchas | 1 day after expiry | – |

Rows are deleted in batches of DELETE_BATCH, so no statement holds locks for
long, and a run stops after MAX_BATCHES per table (the rest goes the next day).
Nothing that is a school record (attendance, fees, marks, messages…) is touched.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from app.core.clock import utcnow
from app.core.config import settings
from app.db import Db, pool

log = logging.getLogger(__name__)

DELETE_BATCH = 5000
MAX_BATCHES = 200
TASK_NAME = "retention"
RUN_EVERY = timedelta(hours=23)
FAILED_JOBS_DAYS = 90


def _purge(db: Db, sql: str, params: tuple) -> int:
    total = 0
    for _ in range(MAX_BATCHES):
        removed = db.execute(f"{sql} LIMIT {DELETE_BATCH}", params).rowcount
        total += removed
        if removed < DELETE_BATCH:
            break
    return total


def run(db: Db) -> dict[str, int]:
    """Delete what is past its retention period. Returns rows deleted per table."""
    now = utcnow()  # naive UTC, for the DATETIME(6) bookkeeping columns
    # a session unused for this long expired long ago (refresh tokens live 30 days idle, 180 at most)
    session_days = max(365, settings.refresh_max_days + 1)
    removed = {
        # TIMESTAMP columns are compared in the connection's (school) time zone, so use NOW()
        "notifications": _purge(db, "DELETE FROM notifications WHERE created_at < NOW() - INTERVAL %s DAY",
                                (settings.notifications_retention_days,)),
        "audit_logs": _purge(db, "DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL %s DAY",
                             (settings.audit_retention_days,)),
        "background_jobs": _purge(
            db, "DELETE FROM background_jobs WHERE finished_at < %s AND (status = 'done' OR finished_at < %s)",
            (now - timedelta(days=settings.jobs_retention_days), now - timedelta(days=FAILED_JOBS_DAYS))),
        "auth_sessions": _purge(db, "DELETE FROM auth_sessions WHERE last_used_at < %s",
                                (now - timedelta(days=session_days),)),
        "auth_login_attempts": _purge(db, "DELETE FROM auth_login_attempts WHERE created_at < %s",
                                      (now - timedelta(days=30),)),
        "auth_reset_requests": _purge(db, "DELETE FROM auth_reset_requests WHERE created_at < %s",
                                      (now - timedelta(days=30),)),
        "password_reset_codes": _purge(db, "DELETE FROM password_reset_codes WHERE created_at < %s",
                                       (now - timedelta(days=30),)),
        "auth_captchas": _purge(db, "DELETE FROM auth_captchas WHERE expires_at < %s", (now - timedelta(days=1),)),
    }
    if any(removed.values()):
        log.info("Clean-up removed old rows: %s", {k: v for k, v in removed.items() if v})
    return removed


def run_if_due() -> dict[str, int] | None:
    """Run the clean-up if no process has run it in the last day (called by the worker)."""
    if not settings.retention_enabled:
        return None
    db = Db(pool)
    try:
        now = utcnow()
        db.execute("INSERT IGNORE INTO scheduled_runs (name, last_run_at) VALUES (%s, %s)",
                   (TASK_NAME, now - RUN_EVERY - timedelta(minutes=1)))
        won = db.execute(
            "UPDATE scheduled_runs SET last_run_at = %s WHERE name = %s AND last_run_at < %s",
            (now, TASK_NAME, now - RUN_EVERY),
        ).rowcount
        return run(db) if won else None
    finally:
        db.close()
