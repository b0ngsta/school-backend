"""Password hashing + stateless HMAC-signed tokens (no extra deps)."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from app.config import SECRET_KEY, TOKEN_EXPIRE_HOURS


# ---------- passwords: stored as "salt$sha256(salt:password)" ----------

def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


# ---------- tokens: base64(payload).base64(hmac) ----------

def _sign(data: bytes) -> str:
    sig = hmac.new(SECRET_KEY.encode(), data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def create_token(user_id: int, user_type: str) -> str:
    payload = {
        "uid": user_id,
        "type": user_type,
        "exp": int(time.time()) + TOKEN_EXPIRE_HOURS * 3600,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{body}.{_sign(raw)}"


def decode_token(token: str) -> dict | None:
    """Returns payload dict, or None if invalid/expired."""
    try:
        body, sig = token.split(".", 1)
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        if not hmac.compare_digest(_sign(raw), sig):
            return None
        payload = json.loads(raw)
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
