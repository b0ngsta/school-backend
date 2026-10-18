"""Short jobs that run after the response has been sent.

Used where the time a request takes must not depend on private data: a
"forgot password" request answers at once, and looking up the account and
issuing the code happens here, so response times reveal nothing about
whether the account exists.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.observability import report_exception

log = logging.getLogger(__name__)

run_inline = False  # tests set this so jobs finish before the response is checked
_executor: ThreadPoolExecutor | None = None


def submit(fn: Callable[..., Any], *args: Any) -> None:
    if run_inline:
        _run(fn, *args)
        return
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="background")
    _executor.submit(_run, fn, *args)


def shutdown(wait: bool = True) -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None


def _run(fn: Callable[..., Any], *args: Any) -> None:
    try:
        fn(*args)
    except Exception as exc:  # a background failure must never be silent, nor crash the worker
        log.exception("Background job %s failed", getattr(fn, "__name__", fn))
        report_exception(exc)
