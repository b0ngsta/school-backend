"""Sending email from services.

`send_after_commit` is the normal entry point: the email leaves only once the
database change that caused it is committed (a rolled-back reset never sends
a code). Slow backends (SMTP) deliver through a durable background job stored
in the same transaction (services/background.py), so requests never wait for
the mail server, a restart loses nothing, and failed deliveries are retried
with backoff before being logged and reported to Sentry. Fast backends (the
console and memory mailers) deliver right after the commit, as before.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import asdict

from app.core.observability import report_exception
from app.db import Db
from app.mail import OutgoingEmail, get_mailer
from app.services import background

log = logging.getLogger(__name__)

RETRY_DELAYS_SECONDS = (2, 10, 30)


def send_after_commit(db: Db, messages: Sequence[OutgoingEmail]) -> None:
    batch = [m for m in messages if m.to]
    if not batch:
        return
    if get_mailer().background and db.in_transaction:
        background.enqueue(db, _deliver_job, [asdict(m) for m in batch])  # commits with the change
    elif db.in_transaction:
        db.on_commit(lambda: send(batch))
    else:
        send(batch)


def send(messages: Sequence[OutgoingEmail]) -> None:
    if not messages:
        return
    mailer = get_mailer()
    if mailer.background:
        background.submit(_deliver_job, [asdict(m) for m in messages])
    else:
        _deliver(list(messages), delays=())


@background.task(max_attempts=4)
def _deliver_job(messages: list[dict]) -> None:
    """Background job: one delivery attempt; the queue retries with backoff."""
    get_mailer().send([OutgoingEmail(**m) for m in messages])


def shutdown(wait: bool = True) -> None:
    """Kept for app.main: emails are background jobs now (stopped with the worker)."""


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
