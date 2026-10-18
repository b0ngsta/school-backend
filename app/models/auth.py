from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.models.common import PASSWORD_MAX_LENGTH, UserType


class LoginRequest(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class TokenResponse(BaseModel):
    """Returned by login, refresh and change-password.

    The first five fields are the original login response; the rest were
    added for refresh-token support (older clients can ignore them).
    """

    access_token: str
    token_type: str = "bearer"
    user_id: int
    user_type: UserType
    full_name: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    must_change_password: bool = False


LoginResponse = TokenResponse


class RefreshRequest(BaseModel):
    refresh_token: str = Field(max_length=200)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)


class ForgotPasswordRequest(BaseModel):
    login: str = Field(min_length=1, max_length=120, description="Username or email address")


class ForgotPasswordResponse(BaseModel):
    requested: bool = True
    code_expires_in: int


class VerifyResetCodeRequest(BaseModel):
    login: str = Field(min_length=1, max_length=120, description="The same username/email as in forgot-password")
    code: str = Field(min_length=1, max_length=20)


class ResetPasswordRequest(VerifyResetCodeRequest):
    new_password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller, resolved once per request."""

    id: int
    username: str
    full_name: str
    email: str | None
    phone: str | None
    user_type: str
    is_active: int
    session_id: str
    must_change_password: bool

    def public(self) -> dict:
        """The /auth/me payload (unchanged from the original API)."""
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "user_type": self.user_type,
            "is_active": self.is_active,
        }
