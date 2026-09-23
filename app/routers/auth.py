from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import get_current_user
from app.schemas import LoginRequest, LoginResponse
from app.security import create_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, password_hash, full_name, user_type, is_active "
            "FROM users WHERE username = %s",
            (body.username,),
        )
        user = cur.fetchone()
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if not user["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    token = create_token(user["id"], user["user_type"])
    return LoginResponse(
        access_token=token,
        user_id=user["id"],
        user_type=user["user_type"],
        full_name=user["full_name"],
    )


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user
