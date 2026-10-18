"""Input checks shared by the JSON forms (Pydantic models) and the CSV import."""
from __future__ import annotations

import re

EMAIL_MAX_LENGTH = 120  # users.email

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_EMAIL_RE = re.compile(rf"^[A-Za-z0-9.!#$%&'*+/=?^_`{{|}}~-]+@{_LABEL}(?:\.{_LABEL})+$")

INVALID_EMAIL = "Enter a valid email address"
EMAIL_REQUIRED = "Email is required"


def is_valid_email(value: str) -> bool:
    return len(value) <= EMAIL_MAX_LENGTH and bool(_EMAIL_RE.match(value))


def clean_email(value: str | None) -> str | None:
    """Trim and check an email address. Blank values are passed through
    unchanged (the forms always allowed an empty email)."""
    if value is None or not value.strip():
        return value
    value = value.strip()
    if not is_valid_email(value):
        raise ValueError(INVALID_EMAIL)
    return value
