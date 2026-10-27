"""A student's attendance for the session and for one month (student app).

Working days = days − Sundays − holidays that fall on other days, counted
only UP TO TODAY: a day that hasn't happened yet can't be missed. Today counts
once the student's attendance for today has been marked. A half day counts 0.5.

    On 26 Oct: 1–25 Oct = 25 days − 4 Sundays − 2 holidays = 19 working days
    (+ today once marked); 17 present → 89%

`working_days_total` is still the whole month / session, for "x of y" displays.
Before Oct 2026 the percentage was over the whole month / session, future days
included, so a student with full attendance showed about 50% in October.
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


def _is_working_day(d: date, holidays: dict[date, str]) -> bool:
    return d.weekday() != SUNDAY and d not in holidays


def _month_counts(year: int, month: int, holidays: dict[date, str], records: dict[date, str],
                  today: date | None = None) -> dict:
    today = today or school_today()
    days = calendar.monthrange(year, month)[1]
    sundays = sum(1 for d in range(1, days + 1) if date(year, month, d).weekday() == SUNDAY)
    off = sum(1 for d in holidays if (d.year, d.month) == (year, month) and d.weekday() != SUNDAY)
    counts = {"present": 0, "absent": 0, "half_day": 0, "leave": 0}
    for d, status in records.items():
        if (d.year, d.month) == (year, month) and status in counts:
            counts[status] += 1
    total = days - sundays - off
    # elapsed working days: before today, plus today once it has been marked
    working = sum(
        1 for n in range(1, days + 1)
        if _is_working_day(day := date(year, month, n), holidays) and (day < today or (day == today and day in records))
    )
    return {
        "year": year, "month": month, "days": days, "sundays": sundays, "holidays": off,
        "working_days": working, "working_days_total": total,
        **counts, "percent": _percent(counts["present"], counts["half_day"], working),
    }


def overview(db: Db, student_id: int) -> dict:
    """Session totals + one entry per month from the session start to this month."""
    session = current_session(db)
    start, end = session["start"], session["end"]
    holidays, records = _holidays(db, start, end), _records(db, student_id, start, end)
    today = school_today()
    all_months = [_month_counts(y, m, holidays, records, today) for y, m in _months(start, end)]
    shown = [m for m in all_months if (m["year"], m["month"]) <= (today.year, today.month)]
    totals = {key: sum(m[key] for m in all_months)
              for key in ("working_days", "working_days_total", "present", "absent", "half_day", "leave",
                          "holidays")}
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
    today = school_today()
    summary = {"present": 0, "half_day": 0, "absent": 0, "leave": 0, "holiday": 0, "not_marked": 0}
    days = []
    d = first
    while d <= last:
        status = records.get(d)
        if status is None:
            status = "holiday" if d in holidays else "sunday" if d.weekday() == SUNDAY else "not_marked"
        # a day still to come isn't "not marked" yet
        counted = status != "not_marked" or d < today
        if counted and status in summary and not (status == "holiday" and d.weekday() == SUNDAY):
            summary[status] += 1
        days.append({"date": d, "status": status, "holiday": holidays.get(d), "is_future": d > today})
        d += timedelta(days=1)
    counts = _month_counts(year, month_no, holidays, records, today)
    return {"year": year, "month": month_no, "days": days, "summary": summary,
            "working_days": counts["working_days"], "working_days_total": counts["working_days_total"],
            "percent": counts["percent"]}
