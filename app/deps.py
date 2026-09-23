from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import db_cursor
from app.security import decode_token

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, username, full_name, email, phone, user_type, is_active "
            "FROM users WHERE id = %s",
            (payload["uid"],),
        )
        user = cur.fetchone()
    if not user or not user["is_active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_roles(*roles: str):
    """Dependency factory: only allow the given user types."""

    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user["user_type"] not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role: {', '.join(roles)}",
            )
        return user

    return checker
