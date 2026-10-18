"""Access log, JSON log format and Sentry reporting."""
import json
import logging

import sentry_sdk
from fastapi.testclient import TestClient
from helpers import bearer, login

from app.core import observability
from app.main import app
from app.services import notices as notice_service


def test_every_request_is_logged_with_user_and_timing(client, caplog):
    tokens = login(client, "admin")
    with caplog.at_level(logging.INFO, logger="app.access"):
        r = client.get("/notices?limit=5", headers=bearer(tokens))
    record = next(rec for rec in caplog.records if getattr(rec, "path", None) == "/notices")
    assert record.status == 200
    assert record.user_id == tokens["user_id"]
    assert record.duration_ms >= 0
    assert "limit" not in record.getMessage()  # query strings are never logged
    assert r.headers["x-request-id"]


def test_json_formatter_produces_one_object_per_line():
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    record.status = 404
    line = observability.JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["msg"] == "hello world" and payload["level"] == "WARNING" and payload["status"] == 404
    assert "\n" not in line


def test_unhandled_errors_reach_sentry_without_request_bodies(client, monkeypatch):
    events = []
    sentry_sdk.init(dsn="https://public@sentry.example.invalid/1", before_send=lambda e, _: events.append(e),
                    send_default_pii=False, max_request_body_size="never", default_integrations=False)
    monkeypatch.setattr(observability, "_sentry_enabled", True)

    def boom(*_args, **_kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(notice_service, "list_notices", boom)
    tokens = login(client, "admin")
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/notices", headers=bearer(tokens))
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error", "request_id": r.headers["x-request-id"]}
    [event] = events
    assert event["exception"]["values"][0]["value"] == "database exploded"
    assert event["tags"]["request_id"] == r.headers["x-request-id"]
    assert event["user"] == {"id": str(tokens["user_id"])}
    sentry_sdk.init(dsn=None)
