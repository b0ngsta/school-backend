"""HTTP plumbing: the default JSON response class and the request-context /
security-headers / access-log middleware."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import client_ip_var, request_id_var, request_state_var, user_agent_var
from app.core.file_urls import sign_file_fields


class ApiJSONResponse(JSONResponse):
    """Default response class: signs every stored file path on the way out,
    so no endpoint can accidentally leak an unsigned upload link."""

    def render(self, content: Any) -> bytes:
        return super().render(sign_file_fields(content))


access_log = logging.getLogger("app.access")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_SECURITY_HEADERS = (
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
    ("cache-control", "no-store"),
)


class RequestContextMiddleware:
    """Assigns a request id, records the client address for audit logging,
    adds security headers to every response and writes one access-log line
    per request (pure ASGI, no body buffering)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        incoming_id = headers.get("x-request-id", "")
        rid = incoming_id if _SAFE_REQUEST_ID.match(incoming_id) else uuid.uuid4().hex
        client = scope.get("client")

        request_id_var.set(rid)
        client_ip_var.set(client[0] if client else None)
        user_agent_var.set(headers.get("user-agent", "")[:255] or None)
        state: dict = {}
        request_state_var.set(state)
        started = time.perf_counter()
        status = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers["x-request-id"] = rid
                for name, value in _SECURITY_HEADERS:
                    if name not in response_headers:
                        response_headers[name] = value
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            path = scope.get("path", "")
            access_log.log(
                logging.DEBUG if path == "/health" else logging.INFO,
                "%s %s %s", scope.get("method"), path, status,
                extra={
                    "method": scope.get("method"),
                    "path": path,  # never the query string (signed links carry signatures)
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client_ip": client[0] if client else None,
                    "user_id": state.get("user_id"),
                },
            )


class BodySizeLimitMiddleware:
    """Rejects request bodies above a per-path limit (HTTP 413) before the
    upload is read, instead of spooling a huge file to disk first. Paths
    without a limit are untouched."""

    def __init__(self, app: ASGIApp, limits: dict[str, int]):
        self.app = app
        self.limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self.limits.get(scope.get("path", "")) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return

        length = dict(scope.get("headers", [])).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            await self._too_large(send, limit)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:  # no/false Content-Length: stop reading
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, counting_receive, send)

    @staticmethod
    async def _too_large(send: Send, limit: int) -> None:
        body = json.dumps({"detail": f"Upload too large (max {limit // (1024 * 1024)} MB in total)"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})
