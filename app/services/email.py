"""Sending email from services.

`send_after_commit` is the normal entry point: the email leaves only once the
database change that caused it is committed (a rolled-back reset never sends
a code). Slow backends (SMTP) deliver on a small background thread pool, so
requests never wait for the mail server, and failed deliveries are retried a
few times before being logged and reported to Sentry.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from app.core.observability import report_exception
from app.db import Db
from app.mail import OutgoingEmail, get_mailer

log = logging.getLogger(__name__)

RETRY_DELAYS_SECONDS = (2, 10, 30)
_executor: ThreadPoolExecutor | None = None


def send_after_commit(db: Db, messages: Sequence[OutgoingEmail]) -> None:
    batch = [m for m in messages if m.to]
    if batch:
        db.on_commit(lambda: send(batch))


def send(messages: Sequence[OutgoingEmail]) -> None:
    if not messages:
        return
    mailer = get_mailer()
    if mailer.background:
        _pool().submit(_deliver, list(messages))
    else:
        _deliver(list(messages), delays=())


def shutdown(wait: bool = True) -> None:
    """Let queued emails finish (called when the app stops)."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="email")
    return _executor


def _deliver(messages: list[OutgoingEmail], delays: Sequence[int] = RETRY_DELAYS_SECONDS) -> None:
    mailer = get_mailer()
    for attempt in range(len(delays) + 1):
        try:
            mailer.send(messages)
            return
        except Exception as exc:
            if attempt < len(delays):
                log.warning("Email delivery failed (%s); retrying in %ss", exc, delays[attempt])
                time.sleep(delays[attempt])
                continue
            log.error("Email delivery failed for %d message(s): %s", len(messages), exc, exc_info=exc)
            report_exception(exc)
