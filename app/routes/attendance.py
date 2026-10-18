"""Attendance — mark per section per day.

view: managers + teachers assigned to the section
mark: managers + the section's CLASS teacher
"""
from datetime import date

from fastapi import APIRouter, Depends

from app.core.roles import STAFF
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.attendance import AttendanceMark
from app.models.auth import CurrentUser
from app.services import attendance as attendance_service

router = APIRouter(prefix="/attendance", tags=["attendance"])

staff = require_roles(*STAFF)


@router.get("/stats")
def attendance_stats(day: date | None = None, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """School-wide numbers for a day (default today)."""
    return attendance_service.stats(db, day)


@router.get("")
def get_attendance(section_id: int, day: date, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    """Students of a section with their status for the day (null = unmarked)."""
    return attendance_service.get_attendance(db, user, section_id, day)


@router.post("")
def mark_attendance(body: AttendanceMark, user: CurrentUser = Depends(staff), db: Db = Depends(get_db)):
    return attendance_service.mark_attendance(db, user, body)
