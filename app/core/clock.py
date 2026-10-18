"""Time helpers.

Two clocks are used on purpose:

* School time (APP_TIMEZONE, default Asia/Kolkata) for everything a person
  sees: "today", check-in times, CURDATE()/NOW() in SQL (each DB connection
  runs with the same offset, see app/db/pool.py).
* Naive UTC for security bookkeeping (sessions, login attempts) so expiry
  maths never depends on the server's or the database's timezone.
"""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings

SCHOOL_TZ = ZoneInfo(settings.timezone)


def utcnow() -> datetime:
    """Naive UTC datetime, for the DATETIME(6) columns written by the auth layer."""
    return datetime.now(UTC).replace(tzinfo=None)


def school_now() -> datetime:
    return datetime.now(SCHOOL_TZ)


def school_today() -> date:
    return school_now().date()


def mysql_offset() -> str:
    """Current UTC offset of the school timezone as '+05:30' for SET time_zone."""
    offset = school_now().utcoffset()
    total_minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"
