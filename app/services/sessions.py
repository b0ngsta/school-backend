"""Login sessions and refresh-token rotation.

Each login creates a session (one per device). The client holds a refresh
token for it; every refresh rotates the token to the next generation.

Theft protection: presenting an *old* generation means two parties hold the
token, so the whole session is revoked (that device must log in again).

Reliability: an app waking from the background often fires several
requests at once, and each may try to refresh with the same token. For
REFRESH_REUSE_GRACE_SECONDS after a rotation, the previous token is still
accepted and receives the *same* current token, so racing requests (or a
retry after a dropped response) never log the user out.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core import security
from app.core.clock import utcnow
from app.core.config import settings
from app.core.context import client_ip, user_agent
from app.core.errors import Unauthorized
from app.db import Db
from app.services import audit

log = logging.getLogger(__name__)

INVALID_REFRESH = "Invalid or expired refresh token"

# the user columns a token response needs (with the organisation), for one user id
TOKEN_USER_SQL = (
    "SELECT u.id, u.full_name, u.user_type, u.is_active, u.token_version, u.must_change_password, "
    "u.org_id, o.code AS org_code, o.name AS org_name, o.is_active AS org_active "
    "FROM users u JOIN organisations o ON o.id = u.org_id WHERE u.id = %s"
)


@dataclass(frozen=True)
class IssuedSession:
    session_id: str
    refresh_token: str
    refresh_expires_in: int


def _seconds_until(moment: datetime, now: datetime) -> int:
    return max(0, int((moment - now).total_seconds()))


def open_session(db: Db, user_id: int) -> IssuedSession:
    """Create a session for a fresh login. Call inside a transaction."""
    now = utcnow()
    _prune(db, user_id, now)
    session_id, salt = security.new_session_id(), security.new_session_salt()
    absolute_expiry = now + timedelta(days=settings.refresh_max_days)
    expires_at = min(now + timedelta(days=settings.refresh_idle_days), absolute_expiry)
    db.execute(
        "INSERT INTO auth_sessions (id, user_id, salt, generation, created_at, last_used_at, "
        "expires_at, absolute_expires_at, ip, user_agent) "
        "VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s)",
        (session_id, user_id, salt, now, now, expires_at, absolute_expiry, client_ip(), user_agent()),
    )
    return IssuedSession(
        session_id=session_id,
        refresh_token=security.build_refresh_token(session_id, 1, salt),
        refresh_expires_in=_seconds_until(expires_at, now),
    )


def rotate(db: Db, raw_token: str) -> tuple[dict, IssuedSession]:
    """Exchange a refresh token for the next one.

    Returns (user row, new session tokens). Raises Unauthorized otherwise.
    Revocations decided here are committed before the error is raised.
    """
    parts = security.parse_refresh_token(raw_token)
    if parts is None:
        raise Unauthorized(INVALID_REFRESH)

    result: tuple[dict, IssuedSession] | None = None
    with db.transaction():
        row = db.one(
            "SELECT id, user_id, salt, generation, rotated_at, expires_at, absolute_expires_at, revoked_at "
            "FROM auth_sessions WHERE id = %s FOR UPDATE",
            (parts.session_id,),
        )
        if row is None or not parts.matches(row["salt"]) or row["revoked_at"] is not None:
            pass
        else:
            result = _rotate_locked(db, row, parts.generation)

    if result is None:
        raise Unauthorized(INVALID_REFRESH)
    return result


def _rotate_locked(db: Db, row: dict, presented_generation: int) -> tuple[dict, IssuedSession] | None:
    now = utcnow()
    if now >= row["expires_at"] or now >= row["absolute_expires_at"]:
        _revoke(db, row["id"], "expired", now)
        return None

    user = db.one(TOKEN_USER_SQL, (row["user_id"],))
    if user is None or not user["is_active"]:
        _revoke(db, row["id"], "user_inactive", now)
        return None
    if not user["org_active"]:  # the school was disabled; it can be re-enabled, so keep the session
        return None

    current = row["generation"]
    within_grace = (
        row["rotated_at"] is not None
        and (now - row["rotated_at"]).total_seconds() <= settings.refresh_reuse_grace_seconds
    )
    if presented_generation == current:
        generation = current + 1
        expires_at = min(now + timedelta(days=settings.refresh_idle_days), row["absolute_expires_at"])
        db.execute(
            "UPDATE auth_sessions SET generation = %s, rotated_at = %s, last_used_at = %s, "
            "expires_at = %s, ip = %s, user_agent = %s WHERE id = %s",
            (generation, now, now, expires_at, client_ip(), user_agent(), row["id"]),
        )
    elif presented_generation == current - 1 and within_grace:
        # A concurrent/retried refresh: hand out the token that was just issued.
        generation, expires_at = current, row["expires_at"]
    else:
        log.warning("Refresh token reuse detected; revoking session %s", row["id"])
        _revoke(db, row["id"], "reuse_detected", now)
        audit.record(db, row["user_id"], "auth.refresh_reuse_detected", "session", row["id"],
                     {"presented_generation": presented_generation, "current_generation": current})
        return None

    return user, IssuedSession(
        session_id=row["id"],
        refresh_token=security.build_refresh_token(row["id"], generation, row["salt"]),
        refresh_expires_in=_seconds_until(expires_at, now),
    )


def session_id_from_refresh_token(db: Db, raw_token: str | None) -> str | None:
    """The session a valid refresh token belongs to (used by logout)."""
    parts = security.parse_refresh_token(raw_token)
    if parts is None:
        return None
    row = db.one("SELECT salt FROM auth_sessions WHERE id = %s", (parts.session_id,))
    return parts.session_id if row and parts.matches(row["salt"]) else None


def revoke(db: Db, session_id: str, reason: str) -> None:
    _revoke(db, session_id, reason, utcnow())


def revoke_all(db: Db, user_id: int, reason: str) -> None:
    db.execute(
        "UPDATE auth_sessions SET revoked_at = %s, revoke_reason = %s "
        "WHERE user_id = %s AND revoked_at IS NULL",
        (utcnow(), reason, user_id),
    )


def _revoke(db: Db, session_id: str, reason: str, now: datetime) -> None:
    db.execute(
        "UPDATE auth_sessions SET revoked_at = %s, revoke_reason = %s "
        "WHERE id = %s AND revoked_at IS NULL",
        (now, reason, session_id),
    )


def _prune(db: Db, user_id: int, now: datetime) -> None:
    """Drop dead sessions and keep at most MAX_SESSIONS_PER_USER - 1 live ones
    (the new login takes the last slot; the least recently used device is
    signed out)."""
    db.execute(
        "DELETE FROM auth_sessions WHERE user_id = %s AND "
        "(revoked_at IS NOT NULL OR expires_at < %s OR absolute_expires_at < %s)",
        (user_id, now, now),
    )
    live = db.all(
        "SELECT id FROM auth_sessions WHERE user_id = %s ORDER BY last_used_at DESC, created_at DESC",
        (user_id,),
    )
    for extra in live[settings.max_sessions_per_user - 1:]:
        _revoke(db, extra["id"], "session_limit", now)
