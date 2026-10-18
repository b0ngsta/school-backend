"""Login, token refresh, logout, password change and forgotten passwords."""
from fastapi import APIRouter, Depends

from app.db import Db
from app.dependencies import get_current_user, get_db, get_optional_user
from app.models.auth import (
    ChangePasswordRequest,
    CurrentUser,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyResetCodeRequest,
)
from app.services import auth as auth_service
from app.services import password_reset

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Db = Depends(get_db)):
    return auth_service.login(db, body.username, body.password)


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return user.public()


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Db = Depends(get_db)):
    """Exchange a refresh token for a new access + refresh token pair.
    Call it when an API request returns 401, or when the app resumes."""
    return auth_service.refresh(db, body.refresh_token)


@router.post("/logout")
def logout(
    body: LogoutRequest | None = None,
    user: CurrentUser | None = Depends(get_optional_user),
    db: Db = Depends(get_db),
):
    """End this device's session. Works with the access token, or with the
    refresh token in the body if the access token has already expired."""
    auth_service.logout(db, user, body.refresh_token if body else None)
    return {"logged_out": True}


@router.post("/logout-all")
def logout_all(user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    """Sign out every device of the current user."""
    auth_service.logout_everywhere(db, user)
    return {"logged_out": True}


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Db = Depends(get_db),
):
    """Change the caller's password. Other devices are signed out; the
    response carries fresh tokens for this one."""
    return auth_service.change_password(db, user, body.current_password, body.new_password)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(body: ForgotPasswordRequest, db: Db = Depends(get_db)):
    """Email a 6-digit reset code. `login` is a username or an email address.
    The reply is the same whether or not an account matches."""
    return ForgotPasswordResponse(code_expires_in=password_reset.request_code(db, body.login))


@router.post("/verify-reset-code")
def verify_reset_code(body: VerifyResetCodeRequest, db: Db = Depends(get_db)):
    """Optional: check the code before asking for the new password. The code
    stays valid; wrong codes count against the attempt limit."""
    password_reset.verify_code(db, body.login, body.code)
    return {"valid": True}


@router.post("/reset-password", response_model=TokenResponse)
def reset_password(body: ResetPasswordRequest, db: Db = Depends(get_db)):
    """Set a new password with the emailed code. Every device is signed out;
    the response logs this one in."""
    return password_reset.reset_password(db, body.login, body.code, body.new_password)
