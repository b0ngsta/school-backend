"""Organisations (schools), public part.

    GET /organisations/{code}  -> {code, name}, for login screens
                                  ("Signing in to XYZ Public School").

Creating, renaming, disabling and watching schools is the platform
operator's job: see routes/platform.py (/platform/*, X-Platform-Secret).
"""
from fastapi import APIRouter, Depends

from app.db import Db
from app.dependencies import get_db
from app.services import organisations

router = APIRouter(prefix="/organisations", tags=["organisations"])


@router.get("/{code}")
def school_by_code(code: str, db: Db = Depends(get_db)):
    """The school's display name for a code (404 if unknown or disabled)."""
    return organisations.public_info(db, code)
