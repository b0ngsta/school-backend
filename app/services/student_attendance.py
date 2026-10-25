"""A student's attendance for the session and for one month (student app).

Working days of a month = its days − Sundays − holidays that fall on other
days. Percentages are over ALL working days of the month / session (future
days included, as the school asked), with a half day counting 0.5:

    October: 31 days − 4 Sundays − 2 holidays = 25 working days; 16 present → 64%
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from app.core.clock import school_today
from app.core.errors import BadRequest
from app.db import Db
from app.services.school_profile import current_session

SUNDAY = 6


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _percent(present: int, half_days: int, working: int, digits: int = 0) -> float | None:
    if not working:
        return None
    value = round((present + half_days * 0.5) * 100 / working, digits)
    return int(value) if digits == 0 else value


def _holidays(db: Db, start: date, end: date) -> dict[date, str]:
    return {r["date"]: r["name"] for r in db.all(
        "SELECT date, name FROM holidays WHERE org_id = %s AND date BETWEEN %s AND %s", (db.org, start, end))}


def _records(db: Db, student_id: int, start: date, end: date) -> dict[date, str]:
    return {r["date"]: r["status"] for r in db.all(
        "SELECT date, status FROM attendance WHERE org_id = %s AND student_id = %s AND date BETWEEN %s AND %s",
        (db.org, student_id, start, end))}


def _month_counts(year: int, month: int, holidays: dict[date, str], records: dict[date, str]) -> dict:
    days = calendar.monthrange(year, month)[1]
    sundays = sum(1 for d in range(1, days + 1) if date(year, month, d).weekday() == SUNDAY)
    off = sum(1 for d in holidays if (d.year, d.month) == (year, month) and d.weekday() != SUNDAY)
    counts = {"present": 0, "absent": 0, "half_day": 0, "leave": 0}
    for d, status in records.items():
        if (d.year, d.month) == (year, month) and status in counts:
            counts[status] += 1
    working = days - sundays - off
    return {
        "year": year, "month": month, "days": days, "sundays": sundays, "holidays": off, "working_days": working,
        **counts, "percent": _percent(counts["present"], counts["half_day"], working),
    }


def overview(db: Db, student_id: int) -> dict:
    """Session totals + one entry per month from the session start to this month."""
    session = current_session(db)
    start, end = session["start"], session["end"]
    holidays, records = _holidays(db, start, end), _records(db, student_id, start, end)
    all_months = [_month_counts(y, m, holidays, records) for y, m in _months(start, end)]
    today = school_today()
    shown = [m for m in all_months if (m["year"], m["month"]) <= (today.year, today.month)]
    totals = {key: sum(m[key] for m in all_months)
              for key in ("working_days", "present", "absent", "half_day", "leave", "holidays")}
    totals["percent"] = _percent(totals["present"], totals["half_day"], totals["working_days"], 1)
    return {"session": session, "totals": totals, "months": shown}


def month(db: Db, student_id: int, year: int, month_no: int) -> dict:
    """Every day of the month with its status: present / absent / leave /
    half_day (marked), else holiday / sunday, else not_marked."""
    if not (1 <= month_no <= 12 and 2000 <= year <= 2100):
        raise BadRequest("Invalid month")
    first = date(year, month_no, 1)
    last = first + timedelta(days=calendar.monthrange(year, month_no)[1] - 1)
    holidays, records = _holidays(db, first, last), _records(db, student_id, first, last)
    summary = {"present": 0, "half_day": 0, "absent": 0, "leave": 0, "holiday": 0, "not_marked": 0}
    days = []
    d = first
    while d <= last:
        status = records.get(d)
        if status is None:
            status = "holiday" if d in holidays else "sunday" if d.weekday() == SUNDAY else "not_marked"
        if status in summary and not (status == "holiday" and d.weekday() == SUNDAY):
            summary[status] += 1
        days.append({"date": d, "status": status, "holiday": holidays.get(d)})
        d += timedelta(days=1)
    counts = _month_counts(year, month_no, holidays, records)
    return {"year": year, "month": month_no, "days": days, "summary": summary,
            "working_days": counts["working_days"], "percent": counts["percent"]}
