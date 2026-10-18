"""Role-aware dashboard stats, weekly attendance series, activity feed."""
from fastapi import APIRouter, Depends

from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

staff = require_roles(*STAFF)
manager = require_roles(*MANAGERS)


@router.get("/attendance-week")
def attendance_week(user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Last 7 days school-wide attendance percentage."""
    return dashboard_service.attendance_week(db)


@router.get("/activities")
def activities(actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    """Latest events across modules (newest first)."""
    return dashboard_service.activities(db)


@router.get("/stats")
def stats(user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return dashboard_service.stats(db, user)
