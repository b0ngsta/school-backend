"""Domain errors raised by services, and the handlers that turn them into
HTTP responses.

Every error response keeps the original shape: {"detail": "<message>"}.
"""
from __future__ import annotations

import logging
import re

import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.context import request_id

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    default_headers: dict[str, str] = {}

    def __init__(self, detail: str, *, headers: dict[str, str] | None = None, extra: dict | None = None):
        super().__init__(detail)
        self.detail = detail
        self.headers = {**self.default_headers, **(headers or {})}
        self.extra = extra or {}  # more fields next to "detail" (e.g. an import's per-row report)


class BadRequest(AppError):
    status_code = 400


class Unauthorized(AppError):
    status_code = 401
    default_headers = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


class Forbidden(AppError):
    status_code = 403


class NotFound(AppError):
    status_code = 404


class Conflict(AppError):
    status_code = 409


class PayloadTooLarge(AppError):
    status_code = 413


class Unprocessable(AppError):
    status_code = 422


class UnsupportedMediaType(AppError):
    status_code = 415


class TooManyRequests(AppError):
    status_code = 429

    def __init__(self, detail: str, *, retry_after_seconds: int):
        super().__init__(detail, headers={"Retry-After": str(max(1, retry_after_seconds))})


class ServiceUnavailable(AppError):
    status_code = 503
    default_headers = {"Retry-After": "2"}


# Friendly messages for UNIQUE keys, by index name.
_DUPLICATE_MESSAGES = {
    "username": "Username already exists",
    "uq_users_org_username": "Username already exists",
    "uq_student_admission_no": "Admission number already exists",
    "uq_student_org_admission_no": "Admission number already exists",
    "uq_student_section_roll": "Roll number already used in this section",
    "uq_users_single_principal": "There is already a principal. Only one principal is allowed.",
    "uq_users_org_principal": "There is already a principal. Only one principal is allowed.",
}


class DuplicateKey(Conflict):
    """A UNIQUE constraint rejected an insert/update (MySQL error 1062)."""

    def __init__(self, detail: str | None = None, *, key: str | None = None):
        self.key = key
        super().__init__(detail or _DUPLICATE_MESSAGES.get(key or "", "Record already exists"))

    @classmethod
    def from_mysql_message(cls, message: str) -> DuplicateKey:
        # "Duplicate entry 'X' for key 'table.index'" (MySQL 8 / TiDB) or "... key 'index'"
        match = re.search(r"for key '(?:[^'.]+\.)?([^']+)'", message or "")
        return cls(key=match.group(1) if match else None)


def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, **exc.extra}, status_code=exc.status_code, headers=exc.headers)


def _integrity_error_handler(_: Request, exc: pymysql.err.IntegrityError) -> JSONResponse:
    code = exc.args[0] if exc.args else None
    if code == 1452:
        return JSONResponse({"detail": "Related record not found"}, status_code=400)
    if code == 1451:
        return JSONResponse({"detail": "Record is still in use"}, status_code=409)
    log.exception("Unhandled integrity error")
    return JSONResponse({"detail": "Invalid data"}, status_code=400)


def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    from app.core.observability import report_exception

    log.error("Unhandled error", exc_info=exc)
    report_exception(exc)
    return JSONResponse(
        {"detail": "Internal server error", "request_id": request_id()}, status_code=500
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(pymysql.err.IntegrityError, _integrity_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
