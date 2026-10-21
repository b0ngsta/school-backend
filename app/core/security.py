"""Cryptographic primitives: password hashing, access tokens (JWT),
refresh-token encoding, password-reset codes and signed file links.

Separate keys are derived from SECRET_KEY for each purpose, so a signature
made for one purpose can never be replayed as another.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "school-api"
JWT_AUDIENCE = "school-app"
CLOCK_SKEW_SECONDS = 30


def _derive_key(purpose: str) -> bytes:
    return hmac.new(settings.secret_key.encode(), f"school-api:{purpose}".encode(), hashlib.sha256).digest()


_ACCESS_KEY = _derive_key("access-token")
_REFRESH_KEY = _derive_key("refresh-token")
_FILE_KEY = _derive_key("file-url")
_RESET_KEY = _derive_key("password-reset-code")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


# ======================= passwords =======================
# Argon2id with OWASP-recommended parameters (19 MiB, 2 passes). Older
# accounts carry "salt$sha256" hashes; they are verified once and upgraded
# to Argon2 on the next successful login.

_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> tuple[bool, bool]:
    """Returns (matches, needs_rehash)."""
    if stored_hash.startswith("$argon2"):
        try:
            _hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, False
        return True, _hasher.check_needs_rehash(stored_hash)
    return _verify_legacy_sha256(password, stored_hash), True


def _verify_legacy_sha256(password: str, stored_hash: str) -> bool:
    salt, sep, _ = stored_hash.partition("$")
    if not sep:
        return False
    digest = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return hmac.compare_digest(f"{salt}${digest}", stored_hash)


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check, so unknown usernames can't be
    told apart from wrong passwords by timing."""
    try:
        _hasher.verify(_DUMMY_HASH, password)
    except VerificationError:
        pass


# ======================= access tokens =======================

@dataclass(frozen=True)
class AccessClaims:
    user_id: int
    session_id: str
    token_version: int
    role: str
    org_id: int | None = None  # absent in tokens issued before organisations existed


class TokenError(Exception):
    def __init__(self, expired: bool = False):
        super().__init__("token expired" if expired else "token invalid")
        self.expired = expired


def create_access_token(*, user_id: int, role: str, session_id: str, token_version: int,
                        org_id: int | None = None) -> tuple[str, int]:
    """Returns (token, lifetime_seconds)."""
    lifetime = settings.access_token_minutes * 60
    now = int(time.time())
    payload = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "typ": "access",
        "sub": str(user_id),
        "sid": session_id,
        "ver": token_version,
        "role": role,
        "org": org_id,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, _ACCESS_KEY, algorithm=JWT_ALGORITHM), lifetime


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            _ACCESS_KEY,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "sub", "sid", "ver"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError(expired=True) from exc
    except jwt.PyJWTError as exc:
        raise TokenError() from exc
    if payload.get("typ") != "access":
        raise TokenError()
    try:
        return AccessClaims(
            user_id=int(payload["sub"]),
            session_id=str(payload["sid"]),
            token_version=int(payload["ver"]),
            role=str(payload.get("role", "")),
            org_id=int(payload["org"]) if payload.get("org") is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise TokenError() from exc


# ======================= refresh tokens =======================
# A refresh token is "rt1.<session id>.<generation>.<mac>". The MAC binds the
# session id and generation to a per-session random salt and a server key,
# so the database never stores a usable token, yet the server can re-issue
# the current token for the reuse grace window (see services/sessions.py).

REFRESH_PREFIX = "rt1"


def new_session_id() -> str:
    return secrets.token_hex(16)


def new_session_salt() -> str:
    return secrets.token_hex(32)


def _refresh_mac(session_id: str, generation: int, salt: str) -> str:
    msg = f"{session_id}.{generation}.{salt}".encode()
    return _b64(hmac.new(_REFRESH_KEY, msg, hashlib.sha256).digest())


def build_refresh_token(session_id: str, generation: int, salt: str) -> str:
    return f"{REFRESH_PREFIX}.{session_id}.{generation}.{_refresh_mac(session_id, generation, salt)}"


@dataclass(frozen=True)
class RefreshTokenParts:
    session_id: str
    generation: int
    mac: str

    def matches(self, salt: str) -> bool:
        return hmac.compare_digest(self.mac, _refresh_mac(self.session_id, self.generation, salt))


def parse_refresh_token(token: str | None) -> RefreshTokenParts | None:
    if not token or len(token) > 200:
        return None
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != REFRESH_PREFIX:
        return None
    _, session_id, generation, mac = parts
    if len(session_id) != 32 or not generation.isdigit():
        return None
    return RefreshTokenParts(session_id=session_id, generation=int(generation), mac=mac)


# ======================= password-reset codes =======================
# Six random digits (a number pad on phones, autofill from email). Only a
# keyed hash bound to the account is stored, so a database leak reveals no
# usable code; guessing is limited by expiry, a per-code attempt counter and
# the login throttle.

RESET_CODE_DIGITS = 6


def new_reset_code() -> str:
    return f"{secrets.randbelow(10 ** RESET_CODE_DIGITS):0{RESET_CODE_DIGITS}d}"


def reset_code_digest(user_id: int, code: str) -> str:
    return hmac.new(_RESET_KEY, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def reset_code_matches(user_id: int, code: str, stored_digest: str) -> bool:
    return hmac.compare_digest(reset_code_digest(user_id, code), stored_digest)


# ======================= signed file links =======================

def sign_file_path(relative_path: str, expires_at: int) -> str:
    msg = f"{relative_path}:{expires_at}".encode()
    return _b64(hmac.new(_FILE_KEY, msg, hashlib.sha256).digest()[:16])


def verify_file_signature(relative_path: str, expires_at: int, signature: str) -> bool:
    if expires_at < time.time():
        return False
    return hmac.compare_digest(sign_file_path(relative_path, expires_at), signature)
