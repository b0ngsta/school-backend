"""FastAPI dependencies shared by all routes: the request's database session,
the authenticated user and role checks."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.context import set_user_id
from app.core.errors import Forbidden, Unauthorized
from app.db import Db, pool
from app.models.auth import CurrentUser
from app.services import auth as auth_service
from app.services.pagination import MAX_PAGE_SIZE, Page, PageResult

_bearer = HTTPBearer(auto_error=False)

# Reachable while a password change is pending (ENFORCE_PASSWORD_CHANGE=true).
_PASSWORD_CHANGE_PATHS = frozenset(
    {"/auth/me", "/auth/change-password", "/auth/logout", "/auth/logout-all"}
)


def get_db() -> Iterator[Db]:
    db = Db(pool)
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Db = Depends(get_db),
) -> CurrentUser:
    if creds is None:
        raise Unauthorized("Missing token", headers={"WWW-Authenticate": "Bearer"})
    user = auth_service.authenticate(db, creds.credentials)
    set_user_id(user.id)
    if (
        settings.enforce_password_change
        and user.must_change_password
        and request.url.path not in _PASSWORD_CHANGE_PATHS
    ):
        raise Forbidden("Password change required")
    return user


def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Db = Depends(get_db),
) -> CurrentUser | None:
    if creds is None:
        return None
    try:
        return auth_service.authenticate(db, creds.credentials)
    except Unauthorized:
        return None


def require_roles(*roles: str):
    """Dependency factory: only allow the given user types."""
    allowed = frozenset(roles)
    message = f"Requires role: {', '.join(roles)}"

    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.user_type not in allowed:
            raise Forbidden(message)
        return user

    return checker


def page_params(
    limit: int | None = Query(
        None, ge=1, le=MAX_PAGE_SIZE, description="Page size. Leave out to get the full list (as before)."
    ),
    offset: int = Query(0, ge=0, description="Rows to skip (with limit)."),
) -> Page:
    return Page(limit=limit, offset=offset)


def paged(response: Response, result: PageResult) -> list[dict]:
    """Return the rows; when a page was requested, add X-Total-Count."""
    if result.total is not None:
        response.headers["X-Total-Count"] = str(result.total)
    return result.rows
