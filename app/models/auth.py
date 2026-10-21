from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.models.common import PASSWORD_MAX_LENGTH, UserType


def _org_code():
    return Field(default=None, max_length=30,
                 description="School (organisation) code. Leave out to use the server's DEFAULT_ORG_CODE.")


class LoginRequest(BaseModel):
    org_code: str | None = _org_code()
    username: str = Field(max_length=100)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class TokenResponse(BaseModel):
    """Returned by login, refresh and change-password.

    The first five fields are the original login response; the rest were
    added later (refresh tokens, organisations); older clients can ignore them.
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
    org_id: int
    org_code: str
    org_name: str


LoginResponse = TokenResponse


class RefreshRequest(BaseModel):
    refresh_token: str = Field(max_length=200)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=6, max_length=PASSWORD_MAX_LENGTH)


class ForgotPasswordRequest(BaseModel):
    org_code: str | None = _org_code()
    login: str = Field(min_length=1, max_length=120, description="Username or email address")


class ForgotPasswordResponse(BaseModel):
    requested: bool = True
    code_expires_in: int


class VerifyResetCodeRequest(BaseModel):
    org_code: str | None = _org_code()
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
    org_id: int
    org_code: str
    org_name: str

    def public(self) -> dict:
        """The /auth/me payload (the original fields plus the organisation)."""
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "user_type": self.user_type,
            "is_active": self.is_active,
            "organisation": {"id": self.org_id, "code": self.org_code, "name": self.org_name},
        }
