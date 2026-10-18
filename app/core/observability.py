"""Logging and error tracking.

* LOG_FORMAT=json (default in production): one JSON object per line with
  timestamp, level, logger, message, request_id and user_id, which hosted
  log viewers (Render, Grafana Loki, CloudWatch, Datadog) can filter on.
  LOG_FORMAT=text gives readable lines for local work.
* Every request is logged once by the middleware in core/http.py with
  method, path, status and duration.
* SENTRY_DSN enables Sentry. Request bodies are never sent (they can contain
  passwords) and personal data is off.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.context import request_id, user_id

_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message", "asctime", "color_message", "context",
}
_sentry_enabled = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid, uid = request_id(), user_id()
        if rid:
            payload["request_id"] = rid
        if uid:
            payload["user_id"] = uid
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_") and key not in payload and value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s%(context)s")

    def format(self, record: logging.LogRecord) -> str:
        rid = request_id()
        record.context = f" [{rid[:8]}]" if rid else ""
        return super().format(record)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    # uvicorn: same format; its own access log is replaced by app.access
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def init_sentry(settings: Settings) -> None:
    global _sentry_enabled
    if not settings.sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
    )
    _sentry_enabled = True


def report_exception(exc: BaseException) -> None:
    """Send an unhandled exception to Sentry (if configured) with request context."""
    if not _sentry_enabled:
        return
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        if request_id():
            scope.set_tag("request_id", request_id())
        if user_id():
            scope.set_user({"id": str(user_id())})
        sentry_sdk.capture_exception(exc)
