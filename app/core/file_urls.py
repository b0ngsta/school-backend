"""Signed, expiring links for uploaded files.

Uploaded files are never served publicly. Wherever an API response contains
a stored file path (photo_path, screenshot_path, paper_path) it is replaced
with a link of the same form as before plus a signature:

    uploads/photos/ab12….jpg?exp=1791234567&sig=Q2x…

Clients keep building `<API base>/<photo_path>` exactly as they did, and only
someone who received the link from an authorised API call can open it.

Links stay valid for at least FILE_URL_TTL_HOURS and are identical for
everyone within a time bucket, so browsers and apps can cache the images.
"""
from __future__ import annotations

import os
import time
from typing import Any

from app.core.config import settings
from app.core.security import sign_file_path, verify_file_signature

URL_PREFIX = "uploads"
FILE_FIELDS = frozenset({"photo_path", "screenshot_path", "paper_path"})

_UPLOAD_ROOT = os.path.abspath(settings.upload_dir)


def _expiry(now: float | None = None) -> int:
    ttl = settings.file_url_ttl_hours * 3600
    bucket = max(ttl // 2, 60)
    now = int(now if now is not None else time.time())
    return (now // bucket) * bucket + bucket + ttl


def relative_upload_path(stored_path: str) -> str | None:
    """'uploads/photos/x.jpg' -> 'photos/x.jpg'; None if outside the upload dir."""
    absolute = os.path.abspath(stored_path)
    if not absolute.startswith(_UPLOAD_ROOT + os.sep):
        return None
    return os.path.relpath(absolute, _UPLOAD_ROOT).replace(os.sep, "/")


def signed_url(stored_path: str | None) -> str | None:
    if not stored_path:
        return stored_path
    relative = relative_upload_path(stored_path)
    if relative is None:
        return None
    expires_at = _expiry()
    return f"{URL_PREFIX}/{relative}?exp={expires_at}&sig={sign_file_path(relative, expires_at)}"


def verify_signed_request(relative_path: str, expires_at: int, signature: str) -> bool:
    """True if `uploads/<relative_path>?exp=…&sig=…` is a genuine, unexpired link."""
    return verify_file_signature(relative_path, expires_at, signature)


def sign_file_fields(content: Any) -> Any:
    """Recursively replace stored file paths in a JSON-ready structure."""
    if isinstance(content, dict):
        return {
            key: signed_url(value) if key in FILE_FIELDS and isinstance(value, str) else sign_file_fields(value)
            for key, value in content.items()
        }
    if isinstance(content, list):
        return [sign_file_fields(item) for item in content]
    return content
