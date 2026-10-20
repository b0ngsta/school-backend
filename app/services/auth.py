"""Authentication: login, token refresh, logout and password changes.

Tokens
* Access token: short-lived JWT (ACCESS_TOKEN_EXPIRE_MINUTES, default 15),
  sent as `Authorization: Bearer …`. Every request re-checks the user, the
  session and the user's token_version, so logout, deactivation and password
  changes take effect immediately.
* Refresh token: long-lived, single-use, rotated on every refresh (see
  services/sessions.py). It is what keeps an app logged in after hours or
  days in the background.
"""
from __future__ import annotations

from app.core import security
from app.core.clock import utcnow
from app.core.context import client_ip
from app.core.errors import BadRequest, Forbidden, Unauthorized
from app.db import Db
from app.models.auth import CurrentUser, TokenResponse
from app.services import audit, login_throttle, notifications, sessions

INVALID_CREDENTIALS = "Invalid username or password"
INVALID_TOKEN = "Invalid or expired token"


def _invalid_credentials() -> Unauthorized:
    return Unauthorized(INVALID_CREDENTIALS, headers={"WWW-Authenticate": "Bearer"})


def token_response(user: dict, session: sessions.IssuedSession) -> TokenResponse:
    access_token, expires_in = security.create_access_token(
        user_id=user["id"],
        role=user["user_type"],
        session_id=session.session_id,
        token_version=user["token_version"],
    )
    return TokenResponse(
        access_token=access_token,
        user_id=user["id"],
        user_type=user["user_type"],
        full_name=user["full_name"],
        expires_in=expires_in,
        refresh_token=session.refresh_token,
        refresh_expires_in=session.refresh_expires_in,
        must_change_password=bool(user["must_change_password"]),
    )


# ------------------------------------------------------------------ login

def login(db: Db, username: str, password: str) -> TokenResponse:
    key, ip = login_throttle.account_key(username), client_ip()
    login_throttle.ensure_allowed(db, key, ip)

    user = db.one(
        "SELECT id, password_hash, full_name, user_type, is_active, token_version, must_change_password "
        "FROM users WHERE username = %s",
        (username,),
    )
    if user is None:
        security.burn_password_check(password)
        _record_failure(db, key, ip)
        raise _invalid_credentials()

    matches, needs_rehash = security.verify_password(password, user["password_hash"])
    if not matches:
        _record_failure(db, key, ip)
        raise _invalid_credentials()
    if not user["is_active"]:
        raise Forbidden("Account disabled")

    with db.transaction():
        if needs_rehash:  # upgrade legacy SHA-256 hashes to Argon2 transparently
            db.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                       (security.hash_password(password), user["id"]))
        login_throttle.record(db, key, ip, success=True)
        session = sessions.open_session(db, user["id"])
    return token_response(user, session)


def _record_failure(db: Db, key: str, ip: str | None) -> None:
    with db.transaction():
        login_throttle.record(db, key, ip, success=False)


# --------------------------------------------------------- access tokens

def authenticate(db: Db, token: str) -> CurrentUser:
    try:
        claims = security.decode_access_token(token)
    except security.TokenError as exc:
        description = "expired" if exc.expired else "invalid"
        raise Unauthorized(
            INVALID_TOKEN,
            headers={"WWW-Authenticate": f'Bearer error="invalid_token", error_description="{description}"'},
        ) from exc

    row = db.one(
        "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.user_type, u.is_active, "
        "u.token_version, u.must_change_password, s.id AS session_id, s.revoked_at "
        "FROM users u LEFT JOIN auth_sessions s ON s.id = %s AND s.user_id = u.id "
        "WHERE u.id = %s",
        (claims.session_id, claims.user_id),
    )
    if not row or not row["is_active"]:
        raise Unauthorized("User not found or inactive")
    if (
        row["token_version"] != claims.token_version
        or row["session_id"] is None
        or row["revoked_at"] is not None
    ):
        raise Unauthorized(INVALID_TOKEN)

    return CurrentUser(
        id=row["id"],
        username=row["username"],
        full_name=row["full_name"],
        email=row["email"],
        phone=row["phone"],
        user_type=row["user_type"],
        is_active=row["is_active"],
        session_id=row["session_id"],
        must_change_password=bool(row["must_change_password"]),
    )


# --------------------------------------------------------------- refresh

def refresh(db: Db, refresh_token: str) -> TokenResponse:
    user, session = sessions.rotate(db, refresh_token)
    return token_response(user, session)


# ---------------------------------------------------------------- logout

def logout(db: Db, user: CurrentUser | None, refresh_token: str | None) -> None:
    """Ends the current device's session. Accepts either a still-valid access
    token or the refresh token, so it works even after the access token
    expired. Always succeeds (idempotent)."""
    session_id = user.session_id if user else sessions.session_id_from_refresh_token(db, refresh_token)
    if session_id:
        sessions.revoke(db, session_id, "logout")
        notifications.forget_session_devices(db, session_id)  # no more pushes to this device


def logout_everywhere(db: Db, user: CurrentUser) -> None:
    with db.transaction():
        db.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user.id,))
        sessions.revoke_all(db, user.id, "logout_all")
        notifications.forget_user_devices(db, user.id)
        audit.record(db, user, "auth.logout_all", "user", user.id)


# -------------------------------------------------------------- passwords

def change_password(db: Db, user: CurrentUser, current_password: str, new_password: str) -> TokenResponse:
    """Change the caller's password, sign out every other device and return
    fresh tokens for this one."""
    key, ip = login_throttle.account_key(user.username), client_ip()
    login_throttle.ensure_allowed(db, key, ip)

    row = db.one("SELECT password_hash FROM users WHERE id = %s", (user.id,))
    matches, _ = security.verify_password(current_password, row["password_hash"]) if row else (False, False)
    if not matches:
        _record_failure(db, key, ip)
        raise BadRequest("Current password is incorrect")
    if current_password == new_password:
        raise BadRequest("New password must be different from the current one")

    with db.transaction():
        set_password(db, user.id, new_password, reason="password_changed")
        audit.record(db, user, "auth.password_changed", "user", user.id)
        session = sessions.open_session(db, user.id)
        fresh = db.one(
            "SELECT id, full_name, user_type, token_version, must_change_password FROM users WHERE id = %s",
            (user.id,),
        )
    return token_response(fresh, session)


def set_password(db: Db, user_id: int, new_password: str, *, reason: str) -> None:
    """Store a new password and invalidate every existing token and session
    of that user. Call inside a transaction."""
    db.execute(
        "UPDATE users SET password_hash = %s, token_version = token_version + 1, "
        "must_change_password = 0, password_changed_at = %s WHERE id = %s",
        (security.hash_password(new_password), utcnow(), user_id),
    )
    sessions.revoke_all(db, user_id, reason)
