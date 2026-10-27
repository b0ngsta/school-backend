"""Work that runs after the response: push messages, emails, password-reset
lookups. Jobs are rows in `background_jobs` (migration 0016), so they survive
deploys, crashes and Render's free-tier sleep, and failures are retried.

    @background.task                              # register the function under its name
    def _send_push(payload): ...

    background.enqueue(db, _send_push, payload)   # inside a transaction: the job exists
                                                  # only if the change commits
    background.submit(fn, *args)                  # no request Db at hand: own connection

Arguments are stored as JSON (dates, datetimes and Decimals are kept), and the
payload is encrypted with a key derived from SECRET_KEY: an email job can carry
a password-reset code.

Who runs them
* a worker thread in every API process (`start_worker()`, from app.main;
  JOBS_WORKER=false turns it off). It is woken the moment a job is committed in
  its own process and otherwise looks for due jobs every JOBS_POLL_SECONDS;
* or `python -m app.cli run-jobs` (a separate worker process), or POST
  /internal/jobs/run from a cron.

Delivery is at least once: a failed job is retried with backoff (10 s, 1 min,
5 min, 30 min, 2 h) up to its max_attempts, then kept as 'failed' with the
error. A job left 'running' by a process that died runs again after
JOBS_STUCK_MINUTES. The same worker starts the daily clean-up
(services/retention.py), in one process per day.

Also used where the time a request takes must not depend on private data: a
"forgot password" request answers at once, and the account lookup happens in
a job, so response times reveal nothing about whether the account exists.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.clock import utcnow
from app.core.config import settings
from app.core.observability import report_exception

log = logging.getLogger(__name__)

run_inline = False  # tests set this so jobs run right away (after the commit) instead of being stored

BACKOFF_SECONDS = (10, 60, 300, 1800, 7200)
CLAIM_BATCH = 20
_HANDLER_PREFIX = "app."  # only our own modules may be imported to find a handler

_TASKS: dict[str, Callable[..., Any]] = {}
_fernet = Fernet(base64.urlsafe_b64encode(
    hmac.new(settings.secret_key.encode(), b"school-api:background-jobs", hashlib.sha256).digest()))
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"[:64]

_wake = threading.Event()
_stop = threading.Event()
_worker: threading.Thread | None = None
_fallback: ThreadPoolExecutor | None = None


# ----------------------------------------------------------------- registering

def task(fn: Callable | None = None, *, max_attempts: int = 5):
    """Decorator: make `fn` runnable as a background job (registered as "module:name")."""
    def register(f: Callable) -> Callable:
        name = f"{f.__module__}:{f.__qualname__}"
        f._job_name = name  # type: ignore[attr-defined]
        f._job_attempts = max_attempts  # type: ignore[attr-defined]
        _TASKS[name] = f
        return f
    return register(fn) if fn is not None else register


def _name_of(fn: Callable) -> str:
    name = getattr(fn, "_job_name", None)
    if name is None:
        raise RuntimeError(f"{fn!r} is not a background task: decorate it with @background.task")
    return name


def _handler(name: str) -> Callable:
    if name not in _TASKS:
        module = name.partition(":")[0]
        if module.startswith(_HANDLER_PREFIX):
            importlib.import_module(module)  # registers its tasks
    if name not in _TASKS:
        raise LookupError(f"Unknown background task {name!r}")
    return _TASKS[name]


# ------------------------------------------------------------------- payloads

def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    raise TypeError(f"Can't store {type(value).__name__} in a background job")


def _decode(obj: dict) -> Any:
    if "__datetime__" in obj:
        return datetime.fromisoformat(obj["__datetime__"])
    if "__date__" in obj:
        return date.fromisoformat(obj["__date__"])
    if "__decimal__" in obj:
        return Decimal(obj["__decimal__"])
    return obj


def _seal(args: tuple) -> str:
    return _fernet.encrypt(json.dumps(list(args), default=_encode, separators=(",", ":")).encode()).decode()


def _open(payload: str) -> list:
    return json.loads(_fernet.decrypt(payload.encode()), object_hook=_decode)


# ------------------------------------------------------------------ enqueueing

def enqueue(db, fn: Callable, *args: Any, delay_seconds: int = 0) -> None:
    """Store a job. Inside a transaction it commits (or rolls back) with it."""
    name = _name_of(fn)
    if run_inline:
        if db.in_transaction:
            db.on_commit(lambda: _run_now(fn, *args))
        else:
            _run_now(fn, *args)
        return
    now = utcnow()
    db.execute(
        "INSERT INTO background_jobs (kind, payload, status, attempts, max_attempts, run_after, created_at) "
        "VALUES (%s, %s, 'pending', 0, %s, %s, %s)",
        (name, _seal(args), fn._job_attempts, now + timedelta(seconds=delay_seconds), now),
    )
    if db.in_transaction:
        db.on_commit(_wake.set)
    else:
        _wake.set()


def submit(fn: Callable, *args: Any) -> None:
    """Store a job on a connection of its own (callers without a request Db)."""
    if run_inline:
        _run_now(fn, *args)
        return
    from app.db import Db, pool

    db = Db(pool)
    try:
        enqueue(db, fn, *args)
    except Exception:  # the database is unreachable: still do the work, as before this queue existed
        log.exception("Could not store background job %s; running it in this process", _name_of(fn))
        _fallback_pool().submit(_run_now, fn, *args)
    finally:
        db.close()


def _fallback_pool() -> ThreadPoolExecutor:
    global _fallback
    if _fallback is None:
        _fallback = ThreadPoolExecutor(max_workers=2, thread_name_prefix="background")
    return _fallback


def _run_now(fn: Callable, *args: Any) -> None:
    try:
        fn(*args)
    except Exception as exc:  # a background failure must never be silent, nor break the request
        log.exception("Background job %s failed", getattr(fn, "__name__", fn))
        report_exception(exc)


# --------------------------------------------------------------------- running

def run_pending(limit: int = CLAIM_BATCH) -> int:
    """Run the jobs that are due (up to `limit`). Returns how many ran."""
    from app.db import Db, pool

    db = Db(pool)
    try:
        now = utcnow()
        db.execute(
            "UPDATE background_jobs SET status = 'pending', locked_by = NULL "
            "WHERE status = 'running' AND locked_at < %s",
            (now - timedelta(minutes=settings.jobs_stuck_minutes),),
        )
        due = [r["id"] for r in db.all(
            "SELECT id FROM background_jobs WHERE status = 'pending' AND run_after <= %s "
            "ORDER BY run_after, id LIMIT %s", (now, limit))]
        ran = 0
        for job_id in due:
            claimed = db.execute(
                "UPDATE background_jobs SET status = 'running', locked_by = %s, locked_at = %s, "
                "attempts = attempts + 1 WHERE id = %s AND status = 'pending'",
                (WORKER_ID, utcnow(), job_id),
            ).rowcount
            if claimed:  # another worker may have taken it first
                _run_job(db, job_id)
                ran += 1
        return ran
    finally:
        db.close()


def _run_job(db, job_id: int) -> None:
    row = db.one("SELECT id, kind, payload, attempts, max_attempts FROM background_jobs WHERE id = %s", (job_id,))
    if row is None:
        return
    try:
        fn = _handler(row["kind"])
        args = _open(row["payload"] or "")
    except (LookupError, InvalidToken, ValueError) as exc:  # can never succeed: don't retry
        _finish(db, job_id, "failed", f"{type(exc).__name__}: {exc}")
        log.error("Background job %s (%s) can't run: %s", job_id, row["kind"], exc)
        return
    try:
        fn(*args)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        if row["attempts"] >= row["max_attempts"]:
            _finish(db, job_id, "failed", error)
            log.error("Background job %s (%s) failed for good after %s attempts: %s",
                      job_id, row["kind"], row["attempts"], error, exc_info=exc)
            report_exception(exc)
        else:
            delay = BACKOFF_SECONDS[min(row["attempts"], len(BACKOFF_SECONDS)) - 1]
            db.execute(
                "UPDATE background_jobs SET status = 'pending', locked_by = NULL, run_after = %s, last_error = %s "
                "WHERE id = %s",
                (utcnow() + timedelta(seconds=delay), error, job_id),
            )
            log.warning("Background job %s (%s) failed (attempt %s of %s), retrying in %ss: %s",
                        job_id, row["kind"], row["attempts"], row["max_attempts"], delay, error)
        return
    _finish(db, job_id, "done", None)


def _finish(db, job_id: int, status: str, error: str | None) -> None:
    # a finished job's payload is no longer needed (it may hold personal data or a reset code)
    db.execute(
        "UPDATE background_jobs SET status = %s, finished_at = %s, last_error = %s, locked_by = NULL"
        + (", payload = NULL" if status == "done" else "") + " WHERE id = %s",
        (status, utcnow(), error, job_id),
    )


def retry_failed() -> int:
    """Put every failed job back in the queue (CLI `retry-failed-jobs`)."""
    from app.db import Db, pool

    db = Db(pool)
    try:
        return db.execute(
            "UPDATE background_jobs SET status = 'pending', attempts = 0, run_after = %s, finished_at = NULL "
            "WHERE status = 'failed' AND payload IS NOT NULL", (utcnow(),)
        ).rowcount
    finally:
        db.close()


def queue_status(db) -> dict:
    """Numbers for /platform/system."""
    row = db.one(
        "SELECT COALESCE(SUM(status = 'pending'), 0) AS pending, COALESCE(SUM(status = 'running'), 0) AS running, "
        "COALESCE(SUM(status = 'failed'), 0) AS failed, MIN(CASE WHEN status = 'pending' THEN run_after END) "
        "AS oldest_due FROM background_jobs WHERE status IN ('pending', 'running', 'failed')"
    ) or {}
    oldest = row.get("oldest_due")
    return {
        "pending": int(row.get("pending") or 0),
        "running": int(row.get("running") or 0),
        "failed": int(row.get("failed") or 0),
        "oldest_due_at": (oldest.isoformat(timespec="seconds") + "Z") if oldest else None,
        "worker_in_api": settings.jobs_worker,
    }


# ---------------------------------------------------------------------- worker

def work_forever(poll_seconds: float | None = None) -> None:
    """The worker loop (thread from start_worker, or `python -m app.cli run-jobs --forever`)."""
    from app.services import retention

    poll = poll_seconds or settings.jobs_poll_seconds
    next_daily_check = 0.0
    while not _stop.is_set():
        _wake.clear()
        ran = 0
        try:
            ran = run_pending()
            if time.monotonic() >= next_daily_check:
                next_daily_check = time.monotonic() + 600
                retention.run_if_due()
        except Exception as exc:  # the database may be down for a while: keep trying
            log.warning("Background worker: %s", exc)
            _stop.wait(min(30.0, poll * 4))
            continue
        if ran < CLAIM_BATCH:  # nothing more due right now
            _wake.wait(poll)


def start_worker() -> threading.Thread | None:
    global _worker
    if run_inline or (_worker is not None and _worker.is_alive()):
        return _worker
    _stop.clear()
    _worker = threading.Thread(target=work_forever, name="background-worker", daemon=True)
    _worker.start()
    return _worker


def shutdown(wait: bool = True) -> None:
    """Stop the worker (an interrupted job runs again later) and the fallback pool."""
    global _worker, _fallback
    _stop.set()
    _wake.set()
    if _worker is not None and wait:
        _worker.join(timeout=15)
    _worker = None
    if _fallback is not None:
        _fallback.shutdown(wait=wait)
        _fallback = None
