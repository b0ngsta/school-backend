"""School-wide settings.

- GET /settings/school-timings   any signed-in user (students too: their timetable shows times)
- PUT /settings/school-timings   managers
"""
from fastapi import APIRouter, Depends, Query

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_current_user, get_db, require_roles
from app.models.auth import CurrentUser
from app.models.school_timings import SchoolTimings
from app.services import school_timings as timings_service

router = APIRouter(prefix="/settings", tags=["settings"])

manager = require_roles(*MANAGERS)


@router.get("/school-timings")
def get_school_timings(user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    """`{}` until the timings are set for the first time."""
    return timings_service.get_timings(db)


@router.put("/school-timings")
def save_school_timings(
    body: SchoolTimings,
    force: bool = Query(False, description="Delete timetable slots beyond the new periods_per_day"),
    actor: CurrentUser = Depends(manager),
    db: Db = Depends(get_db),
):
    return timings_service.save_timings(db, actor, body, force)
