"""Platform operator views: every school at a glance, one school in depth,
the audit trail across schools, and support actions (set a new password,
sign a user out). Served under /platform/* to the owner portal
(`work/dashboard`), which calls it server-side with PLATFORM_SECRET.

These functions work across organisations on purpose, so their SQL filters
or groups by `org_id` explicitly instead of reading `db.org`. Anything that
acts inside one school binds the session to it first (`db.bind_org`), so the
shared helpers (audit log, the school's activity feed) behave exactly as in
a signed-in request.

Every cross-school number comes from a handful of GROUP BY org_id queries
(not one query per school), so the cost grows with the data, not with the
number of schools times the number of metrics.

Times: TIMESTAMP columns come back in school time (each connection runs with
the school's UTC offset); the auth tables store naive UTC. Responses carry
ISO-8601 strings with an explicit offset either way.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import Any

from app.core.clock import SCHOOL_TZ, mysql_offset, school_now, school_today, utcnow
from app.core.config import settings
from app.core.errors import NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.platform import PlatformPasswordReset
from app.models.school import SchoolProfileUpdate
from app.services import audit, background, dashboard, organisations, school_profile, sessions
from app.services import auth as auth_service

TREND_DAYS = 14
SCHOOL_DAYS = frozenset(range(6))  # Mon=0 … Sat=5; Sunday is off
ROLES = ("admin", "principal", "sub_admin", "coordinator", "teacher", "driver", "student")
ACTIVITY_MAX_LIMIT = 200


# ------------------------------------------------------------------ helpers

@dataclass(frozen=True)
class _Window:
    today: date
    local_7d: datetime    # school time, for TIMESTAMP columns
    local_30d: datetime
    utc_24h: datetime     # naive UTC, for the auth tables
    utc_7d: datetime
    utc_30d: datetime


def _window() -> _Window:
    now_local = school_now().replace(tzinfo=None)
    now_utc = utcnow()
    return _Window(
        today=school_today(),
        local_7d=now_local - timedelta(days=7),
        local_30d=now_local - timedelta(days=30),
        utc_24h=now_utc - timedelta(hours=24),
        utc_7d=now_utc - timedelta(days=7),
        utc_30d=now_utc - timedelta(days=30),
    )


def _school_iso(value: Any) -> str | None:
    """A naive school-time datetime (TIMESTAMP column) as ISO-8601 with offset."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=SCHOOL_TZ).isoformat(timespec="seconds")
    return str(value)


def _utc_iso(value: Any) -> str | None:
    """A naive UTC datetime (auth tables) as ISO-8601 with offset."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).isoformat(timespec="seconds")
    return str(value)


def _int(value: Any) -> int:
    return int(value or 0)


def _money(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(round(value, 2))
    return float(value or 0)


def _rate(present: int, marked: int) -> float | None:
    return round(present / marked * 100, 1) if marked else None


def _by_org(rows: Iterable[dict]) -> dict[int, dict]:
    return {row["org_id"]: row for row in rows}


def _like(text: str) -> str:
    """`text` as a LIKE pattern matching it anywhere, wildcards escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _org(db: Db, code: str) -> dict:
    org = organisations.find(db, code)
    if org is None:
        raise NotFound("School not found")
    return org


def _org_public(org: dict) -> dict:
    return {
        "id": org["id"],
        "code": org["code"],
        "name": org["name"],
        "is_active": bool(org["is_active"]),
        "created_at": _school_iso(org["created_at"]),
        "updated_at": _school_iso(org["updated_at"]),
    }


def _profile(db: Db, org_id: int) -> dict:
    """The school's own profile (School Info screen in the apps)."""
    row = school_profile.get_profile(db, org_id)
    session = row.pop("session")
    for key in ("id", "code", "name"):
        row.pop(key, None)
    row["session"] = {"label": session["label"], "start": str(session["start"]), "end": str(session["end"])}
    return row


def update_profile(db: Db, code: str, body: SchoolProfileUpdate, operator: str | None) -> dict:
    org = _org(db, code)
    db.bind_org(org["id"])
    school_profile.update_profile(db, None, body, org["id"], operator)
    return _profile(db, org["id"])


def _trend_days() -> list[date]:
    today = school_today()
    return [today - timedelta(days=offset) for offset in range(TREND_DAYS - 1, -1, -1)]


def _attendance_trend(db: Db, org_id: int | None = None) -> list[dict]:
    days = _trend_days()
    sql = ("SELECT date, COUNT(*) AS marked, COALESCE(SUM(status = 'present'), 0) AS present "
           "FROM attendance WHERE date BETWEEN %s AND %s")
    params: list[Any] = [days[0], days[-1]]
    if org_id is not None:
        sql += " AND org_id = %s"
        params.append(org_id)
    by_day = {row["date"]: row for row in db.all(sql + " GROUP BY date", params)}
    out = []
    for day in days:
        row = by_day.get(day, {})
        marked, present = _int(row.get("marked")), _int(row.get("present"))
        out.append({"date": str(day), "marked": marked, "present": present, "rate": _rate(present, marked)})
    return out


def _payments_trend(db: Db, org_id: int | None = None) -> list[dict]:
    days = _trend_days()
    sql = ("SELECT DATE(created_at) AS day, COALESCE(SUM(amount), 0) AS amount, COUNT(*) AS count "
           "FROM transactions WHERE status = 'success' AND created_at >= %s")
    params: list[Any] = [datetime.combine(days[0], dtime.min)]
    if org_id is not None:
        sql += " AND org_id = %s"
        params.append(org_id)
    by_day = {row["day"]: row for row in db.all(sql + " GROUP BY day", params)}
    return [
        {"date": str(day), "amount": _money(by_day.get(day, {}).get("amount")),
         "count": _int(by_day.get(day, {}).get("count"))}
        for day in days
    ]


def _logins_trend(db: Db, org_id: int | None = None) -> list[dict]:
    """Successful and failed sign-ins per school day (attempts are kept 30 days)."""
    days = _trend_days()
    start_utc = datetime.combine(days[0], dtime.min, tzinfo=SCHOOL_TZ).astimezone(UTC).replace(tzinfo=None)
    sql = ("SELECT DATE(CONVERT_TZ(created_at, '+00:00', %s)) AS day, "
           "COALESCE(SUM(success = 1), 0) AS ok, COALESCE(SUM(success = 0), 0) AS failed "
           "FROM auth_login_attempts WHERE created_at >= %s")
    params: list[Any] = [mysql_offset(), start_utc]
    if org_id is not None:
        sql += " AND username LIKE %s"  # throttle keys are "<org id>:<username>"
        params.append(f"{org_id}:%")
    by_day = {row["day"]: row for row in db.all(sql + " GROUP BY day", params)}
    return [
        {"date": str(day), "ok": _int(by_day.get(day, {}).get("ok")),
         "failed": _int(by_day.get(day, {}).get("failed"))}
        for day in days
    ]


# ----------------------------------------------------- every school at once

def _alerts(row: dict, school_day: bool, holiday: bool) -> list[str]:
    """Short codes the portal turns into "needs attention" badges."""
    if not row["is_active"]:
        return ["disabled"]
    alerts = []
    if row["users"] <= 1:
        alerts.append("setup_incomplete")          # nobody added beyond the first admin
    if row["active_users"]["last_30d"] == 0:
        alerts.append("inactive_30d")
    elif row["active_users"]["last_7d"] == 0:
        alerts.append("inactive_7d")
    if school_day and not holiday and row["active_students"] and not row["attendance_today"]["marked"]:
        alerts.append("attendance_not_marked")
    if row["fees"]["overdue"] > 0:
        alerts.append("fees_overdue")
    if row["pending"]["fee_claims"]:
        alerts.append("fee_claims_waiting")
    return alerts


def school_rows(db: Db) -> list[dict]:
    """Every school with its headline numbers, a 14-day attendance strip and
    alerts (10 queries in total, whatever the number of schools)."""
    w = _window()
    orgs = db.all("SELECT id, code, name, is_active, created_at, updated_at FROM organisations ORDER BY name, id")
    users = _by_org(db.all(
        "SELECT org_id, COUNT(*) AS users, "
        "COALESCE(SUM(user_type = 'student'), 0) AS students, "
        "COALESCE(SUM(user_type <> 'student'), 0) AS staff, "
        "COALESCE(SUM(user_type = 'student' AND is_active = 1), 0) AS active_students, "
        "COALESCE(SUM(user_type = 'teacher' AND is_active = 1), 0) AS teachers, "
        "COALESCE(SUM(created_at >= %s), 0) AS new_users_30d "
        "FROM users WHERE left_on IS NULL GROUP BY org_id", (w.local_30d,)))
    attendance = _by_org(db.all(
        "SELECT org_id, COUNT(*) AS marked, COALESCE(SUM(status = 'present'), 0) AS present "
        "FROM attendance WHERE date = %s GROUP BY org_id", (w.today,)))
    staff_in = _by_org(db.all(
        "SELECT org_id, COUNT(*) AS checked_in FROM staff_attendance WHERE date = %s GROUP BY org_id", (w.today,)))
    holidays = {row["org_id"] for row in db.all("SELECT org_id FROM holidays WHERE date = %s", (w.today,))}
    fees = _by_org(db.all(
        "SELECT org_id, "
        "COALESCE(SUM(CASE WHEN status = 'paid' THEN amount END), 0) AS collected, "
        "COALESCE(SUM(CASE WHEN status = 'pending' THEN amount END), 0) AS pending, "
        "COALESCE(SUM(CASE WHEN status = 'pending' AND due_date < %s THEN amount END), 0) AS overdue "
        "FROM fees GROUP BY org_id", (w.today,)))
    payments = _by_org(db.all(
        "SELECT org_id, COALESCE(SUM(amount), 0) AS amount, COUNT(*) AS count FROM transactions "
        "WHERE status = 'success' AND created_at >= %s GROUP BY org_id", (w.local_30d,)))
    pending = _by_org(db.all(
        "SELECT org_id, COALESCE(SUM(kind = 'claim'), 0) AS fee_claims, COALESCE(SUM(kind = 'leave'), 0) AS leaves "
        "FROM (SELECT org_id, 'claim' AS kind FROM fee_claims WHERE status = 'pending' "
        "      UNION ALL SELECT org_id, 'leave' AS kind FROM leave_requests WHERE status = 'pending') AS waiting "
        "GROUP BY org_id"))
    active = _by_org(db.all(
        "SELECT u.org_id, "
        "COUNT(DISTINCT CASE WHEN s.last_used_at >= %s THEN s.user_id END) AS last_24h, "
        "COUNT(DISTINCT CASE WHEN s.last_used_at >= %s THEN s.user_id END) AS last_7d, "
        "COUNT(DISTINCT s.user_id) AS last_30d, MAX(s.last_used_at) AS last_active_at "
        "FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.last_used_at >= %s GROUP BY u.org_id", (w.utc_24h, w.utc_7d, w.utc_30d)))
    days = _trend_days()
    strips: dict[int, dict[date, float | None]] = {}
    for r in db.all(
        "SELECT org_id, date, COUNT(*) AS marked, COALESCE(SUM(status = 'present'), 0) AS present "
        "FROM attendance WHERE date BETWEEN %s AND %s GROUP BY org_id, date", (days[0], days[-1])):
        strips.setdefault(r["org_id"], {})[r["date"]] = _rate(_int(r["present"]), _int(r["marked"]))

    school_day = w.today.weekday() in SCHOOL_DAYS
    rows = []
    for org in orgs:
        oid = org["id"]
        u, a, f = users.get(oid, {}), attendance.get(oid, {}), fees.get(oid, {})
        p, waiting, act = payments.get(oid, {}), pending.get(oid, {}), active.get(oid, {})
        marked, present = _int(a.get("marked")), _int(a.get("present"))
        row = {
            **_org_public(org),
            "users": _int(u.get("users")),
            "students": _int(u.get("students")),          # every student account (as before)
            "staff": _int(u.get("staff")),                # every non-student account (as before)
            "active_students": _int(u.get("active_students")),
            "teachers": _int(u.get("teachers")),
            "new_users_30d": _int(u.get("new_users_30d")),
            "attendance_today": {"marked": marked, "present": present, "rate": _rate(present, marked)},
            # present % per day for the last 14 days, oldest first (None = not marked that day)
            "attendance_14d": [strips.get(oid, {}).get(day) for day in days],
            "staff_checked_in_today": _int(staff_in.get(oid, {}).get("checked_in")),
            "holiday_today": oid in holidays,
            "fees": {
                "collected": _money(f.get("collected")),
                "pending": _money(f.get("pending")),
                "overdue": _money(f.get("overdue")),
                "collected_30d": _money(p.get("amount")),
                "payments_30d": _int(p.get("count")),
            },
            "pending": {"fee_claims": _int(waiting.get("fee_claims")), "leaves": _int(waiting.get("leaves"))},
            "active_users": {
                "last_24h": _int(act.get("last_24h")),
                "last_7d": _int(act.get("last_7d")),
                "last_30d": _int(act.get("last_30d")),
            },
            "last_active_at": _utc_iso(act.get("last_active_at")),
        }
        row["alerts"] = _alerts(row, school_day, oid in holidays)
        rows.append(row)
    return rows


def overview(db: Db) -> dict:
    """Totals across every school, 14-day trends and each school's row."""
    w = _window()
    rows = school_rows(db)
    by_role = {r["user_type"]: _int(r["n"]) for r in db.all(
        "SELECT user_type, COUNT(*) AS n FROM users WHERE is_active = 1 GROUP BY user_type")}
    logins = db.one(
        "SELECT COALESCE(SUM(success = 1), 0) AS ok, COALESCE(SUM(success = 0), 0) AS failed "
        "FROM auth_login_attempts WHERE created_at >= %s", (w.utc_24h,)) or {}
    active_users = db.one(
        "SELECT COUNT(DISTINCT CASE WHEN last_used_at >= %s THEN user_id END) AS last_24h, "
        "COUNT(DISTINCT CASE WHEN last_used_at >= %s THEN user_id END) AS last_7d, "
        "COUNT(DISTINCT user_id) AS last_30d FROM auth_sessions WHERE last_used_at >= %s",
        (w.utc_24h, w.utc_7d, w.utc_30d)) or {}

    def total(pick) -> float:
        return sum(pick(r) for r in rows)

    marked = int(total(lambda r: r["attendance_today"]["marked"]))
    present = int(total(lambda r: r["attendance_today"]["present"]))
    active_rows = [r for r in rows if r["is_active"]]
    return {
        "generated_at": school_now().isoformat(timespec="seconds"),
        "today": str(w.today),
        "school_day": w.today.weekday() in SCHOOL_DAYS,
        "schools": {
            "total": len(rows),
            "active": len(active_rows),
            "disabled": len(rows) - len(active_rows),
            "new_30d": _int(db.scalar("SELECT COUNT(*) FROM organisations WHERE created_at >= %s",
                                      (w.local_30d,))),
            "needing_attention": sum(1 for r in active_rows if r["alerts"]),
        },
        "people": {
            "by_role": {role: by_role.get(role, 0) for role in ROLES},
            "students": by_role.get("student", 0),
            "teachers": by_role.get("teacher", 0),
            "staff": sum(n for role, n in by_role.items() if role != "student"),
            "new_30d": int(total(lambda r: r["new_users_30d"])),
        },
        "attendance_today": {
            "marked": marked,
            "present": present,
            "rate": _rate(present, marked),
            "schools_marked": sum(1 for r in active_rows if r["attendance_today"]["marked"]),
            "schools_expected": sum(1 for r in active_rows if r["active_students"] and not r["holiday_today"]),
        },
        "staff_checked_in_today": int(total(lambda r: r["staff_checked_in_today"])),
        "fees": {
            key: round(total(lambda r, k=key: r["fees"][k]), 2)
            for key in ("collected", "pending", "overdue", "collected_30d")
        } | {"payments_30d": int(total(lambda r: r["fees"]["payments_30d"]))},
        "pending": {
            "fee_claims": int(total(lambda r: r["pending"]["fee_claims"])),
            "leaves": int(total(lambda r: r["pending"]["leaves"])),
        },
        "active_users": {key: _int(active_users.get(key)) for key in ("last_24h", "last_7d", "last_30d")},
        "logins_24h": {"ok": _int(logins.get("ok")), "failed": _int(logins.get("failed"))},
        "trends": {
            "attendance": _attendance_trend(db),
            "payments": _payments_trend(db),
            "logins": _logins_trend(db),
        },
        "organisations": rows,
    }


# --------------------------------------------------------- one school deep

_MANAGER_LIST = ", ".join(f"'{role}'" for role in MANAGERS)


def school_detail(db: Db, code: str) -> dict:
    """Everything worth knowing about one school, in a few round trips."""
    org = _org(db, code)
    oid = org["id"]
    db.bind_org(oid)
    w = _window()
    c = db.one(
        "SELECT "
        "(SELECT COUNT(*) FROM classes WHERE org_id = %(o)s) AS classes, "
        "(SELECT COUNT(*) FROM sections WHERE org_id = %(o)s) AS sections, "
        "(SELECT COUNT(*) FROM vehicles WHERE org_id = %(o)s) AS vehicles, "
        "(SELECT COUNT(*) FROM school_timings WHERE org_id = %(o)s) AS timings_set, "
        "(SELECT COUNT(*) FROM exams WHERE org_id = %(o)s AND end_date >= %(today)s) AS exams_upcoming, "
        "(SELECT COUNT(*) FROM lesson_plans WHERE org_id = %(o)s AND created_at >= %(d30)s) AS lesson_plans_30d, "
        "(SELECT COUNT(*) FROM class_homework WHERE org_id = %(o)s AND created_at >= %(d30)s) AS homework_30d, "
        "(SELECT COUNT(*) FROM notices WHERE org_id = %(o)s AND created_at >= %(d30)s) AS notices_30d, "
        "(SELECT COUNT(*) FROM sms_messages WHERE org_id = %(o)s AND created_at >= %(d30)s) AS sms_30d, "
        "(SELECT COUNT(*) FROM leave_requests WHERE org_id = %(o)s AND status = 'pending') AS pending_leaves, "
        "(SELECT COUNT(*) FROM fee_claims WHERE org_id = %(o)s AND status = 'pending') AS pending_claims, "
        "(SELECT COUNT(*) FROM substitution_requests WHERE org_id = %(o)s AND status = 'pending') "
        "  AS pending_substitutions, "
        "(SELECT COUNT(*) FROM staff_attendance WHERE org_id = %(o)s AND date = %(today)s) AS staff_checked_in, "
        "(SELECT COUNT(*) FROM attendance WHERE org_id = %(o)s AND date = %(today)s) AS marked_today, "
        "(SELECT COALESCE(SUM(status = 'present'), 0) FROM attendance "
        "  WHERE org_id = %(o)s AND date = %(today)s) AS present_today, "
        "(SELECT COUNT(*) FROM holidays WHERE org_id = %(o)s AND date = %(today)s) AS holiday_today, "
        "(SELECT COALESCE(SUM(amount), 0) FROM fees WHERE org_id = %(o)s AND status = 'paid') AS fees_collected, "
        "(SELECT COALESCE(SUM(amount), 0) FROM fees WHERE org_id = %(o)s AND status = 'pending') AS fees_pending, "
        "(SELECT COALESCE(SUM(amount), 0) FROM fees "
        "  WHERE org_id = %(o)s AND status = 'pending' AND due_date < %(today)s) AS fees_overdue, "
        "(SELECT COALESCE(SUM(amount), 0) FROM transactions "
        "  WHERE org_id = %(o)s AND status = 'success' AND created_at >= %(d30)s) AS collected_30d",
        {"o": oid, "today": w.today, "d30": w.local_30d},
    )
    roles = {r["user_type"]: r for r in db.all(
        "SELECT user_type, COUNT(*) AS total, COALESCE(SUM(is_active = 1), 0) AS active, "
        "COALESCE(SUM(must_change_password = 1), 0) AS must_change, "
        "COALESCE(SUM(created_at >= %s), 0) AS new_30d "
        "FROM users WHERE org_id = %s AND left_on IS NULL GROUP BY user_type", (w.local_30d, oid))}
    enquiries = {r["status"]: _int(r["n"]) for r in db.all(
        "SELECT status, COUNT(*) AS n FROM enquiries WHERE org_id = %s GROUP BY status", (oid,))}
    engagement = db.one(
        "SELECT COUNT(DISTINCT CASE WHEN s.last_used_at >= %s THEN s.user_id END) AS last_24h, "
        "COUNT(DISTINCT CASE WHEN s.last_used_at >= %s THEN s.user_id END) AS last_7d, "
        "COUNT(DISTINCT s.user_id) AS last_30d, MAX(s.last_used_at) AS last_active_at "
        "FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE u.org_id = %s AND s.last_used_at >= %s",
        (w.utc_24h, w.utc_7d, oid, w.utc_30d)) or {}
    logins = db.one(
        "SELECT COALESCE(SUM(success = 1), 0) AS ok, COALESCE(SUM(success = 0), 0) AS failed "
        "FROM auth_login_attempts WHERE username LIKE %s AND created_at >= %s",
        (f"{oid}:%", w.utc_24h)) or {}
    managers = db.all(
        "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.user_type, u.is_active, "
        "u.must_change_password, u.created_at, "
        "(SELECT MAX(s.last_used_at) FROM auth_sessions s WHERE s.user_id = u.id) AS last_seen_at "
        f"FROM users u WHERE u.org_id = %s AND u.user_type IN ({_MANAGER_LIST}) "
        f"ORDER BY FIELD(u.user_type, {_MANAGER_LIST}), u.full_name, u.id",
        (oid,))

    marked, present = _int(c["marked_today"]), _int(c["present_today"])
    people = {role: {"total": _int(roles.get(role, {}).get("total")),
                     "active": _int(roles.get(role, {}).get("active"))} for role in ROLES}
    return {
        "organisation": _org_public(org),
        "profile": _profile(db, oid),
        "today": str(w.today),
        "people": {
            "by_role": people,
            "total": sum(_int(r["total"]) for r in roles.values()),
            "active": sum(_int(r["active"]) for r in roles.values()),
            "must_change_password": sum(_int(r["must_change"]) for r in roles.values()),
            "new_30d": sum(_int(r["new_30d"]) for r in roles.values()),
        },
        "structure": {
            "classes": _int(c["classes"]),
            "sections": _int(c["sections"]),
            "vehicles": _int(c["vehicles"]),
            "school_timings_set": bool(c["timings_set"]),
        },
        "attendance": {
            "today": {"marked": marked, "present": present, "rate": _rate(present, marked)},
            "holiday_today": bool(c["holiday_today"]),
            "staff_checked_in_today": _int(c["staff_checked_in"]),
            "trend": _attendance_trend(db, oid),
        },
        "fees": {
            "collected": _money(c["fees_collected"]),
            "pending": _money(c["fees_pending"]),
            "overdue": _money(c["fees_overdue"]),
            "collected_30d": _money(c["collected_30d"]),
            "pending_claims": _int(c["pending_claims"]),
            "trend": _payments_trend(db, oid),
        },
        "academics": {
            "exams_upcoming": _int(c["exams_upcoming"]),
            "lesson_plans_30d": _int(c["lesson_plans_30d"]),
            "homework_30d": _int(c["homework_30d"]),
            "notices_30d": _int(c["notices_30d"]),
            "sms_30d": _int(c["sms_30d"]),
        },
        "pending": {
            "leaves": _int(c["pending_leaves"]),
            "fee_claims": _int(c["pending_claims"]),
            "substitutions": _int(c["pending_substitutions"]),
        },
        "enquiries": {status: enquiries.get(status, 0) for status in ("new", "follow_up", "converted", "closed")},
        "engagement": {
            "active_users": {key: _int(engagement.get(key)) for key in ("last_24h", "last_7d", "last_30d")},
            "last_active_at": _utc_iso(engagement.get("last_active_at")),
            "logins_24h": {"ok": _int(logins.get("ok")), "failed": _int(logins.get("failed"))},
            "logins_trend": _logins_trend(db, oid),
        },
        "managers": [_account(m) for m in managers],
        "recent": [
            {**item, "at": _school_iso(item["at"])} for item in dashboard.activities(db)
        ],
    }


def _account(row: dict) -> dict:
    out = {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "email": row["email"],
        "phone": row["phone"],
        "user_type": row["user_type"],
        "is_active": bool(row["is_active"]),
        "left_on": row["left_on"].isoformat() if row.get("left_on") else None,
        "must_change_password": bool(row["must_change_password"]),
        "created_at": _school_iso(row["created_at"]),
        "last_seen_at": _utc_iso(row["last_seen_at"]),
    }
    for key in ("admission_no", "roll_no", "class_name", "section_name"):
        if key in row:
            out[key] = row[key]
    return out


def school_users(
    db: Db,
    code: str,
    *,
    role: str | None = None,
    query: str | None = None,
    active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """One school's accounts (newest sign-in first is not useful for support;
    they are sorted by name), with class/section for students and when each
    account was last seen."""
    org = _org(db, code)
    where, params = ["u.org_id = %s"], [org["id"]]
    if role:
        where.append("u.user_type = %s")
        params.append(role)
    if active is not None:
        where.append("u.is_active = %s")
        params.append(int(active))
    text = (query or "").strip()
    if text:
        like = _like(text[:100])
        where.append("(u.username LIKE %s OR u.full_name LIKE %s OR u.email LIKE %s OR u.phone LIKE %s "
                     "OR sp.admission_no LIKE %s)")
        params += [like] * 5
    joins = ("FROM users u LEFT JOIN student_profiles sp ON sp.user_id = u.id "
             "LEFT JOIN classes c ON c.id = COALESCE(sp.class_id, sp.last_class_id) "
             "LEFT JOIN sections s ON s.id = COALESCE(sp.section_id, sp.last_section_id) ")
    clause = "WHERE " + " AND ".join(where)
    total = _int(db.scalar(f"SELECT COUNT(*) {joins}{clause}", params))
    rows = db.all(
        "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.user_type, u.is_active, u.left_on, "
        "u.must_change_password, u.created_at, sp.admission_no, sp.roll_no, "
        "c.name AS class_name, s.name AS section_name, "
        "(SELECT MAX(a.last_used_at) FROM auth_sessions a WHERE a.user_id = u.id) AS last_seen_at "
        f"{joins}{clause} ORDER BY u.full_name, u.id LIMIT %s OFFSET %s",
        [*params, limit, offset],
    )
    return [_account(r) for r in rows], total


def _school_user(db: Db, code: str, user_id: int) -> tuple[dict, dict]:
    org = _org(db, code)
    db.bind_org(org["id"])
    user = db.one(
        "SELECT id, username, full_name, user_type FROM users WHERE id = %s AND org_id = %s FOR UPDATE",
        (user_id, org["id"]),
    )
    if user is None:
        raise NotFound("User not found")
    return org, user


def set_user_password(db: Db, code: str, user_id: int, body: PlatformPasswordReset,
                      operator: str | None) -> dict:
    """Give a school account a new password (e.g. a locked-out school admin).
    Every session of the user ends; by default they must pick their own
    password at the next login."""
    with db.transaction():
        org, user = _school_user(db, code, user_id)
        auth_service.set_password(db, user["id"], body.new_password, reason="password_set_platform")
        if body.must_change_password:
            db.execute("UPDATE users SET must_change_password = 1 WHERE id = %s", (user["id"],))
        audit.record(db, None, "platform.user_password_set", "user", user["id"], {
            "username": user["username"], "must_change_password": body.must_change_password,
            **({"operator": operator} if operator else {})})
    return {"id": user["id"], "username": user["username"], "org_code": org["code"],
            "must_change_password": body.must_change_password, "sessions_revoked": True}


def sign_out_user(db: Db, code: str, user_id: int, operator: str | None) -> dict:
    """End every session of a school account (its tokens stop working now)."""
    with db.transaction():
        org, user = _school_user(db, code, user_id)
        db.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user["id"],))
        sessions.revoke_all(db, user["id"], "revoked_platform")
        audit.record(db, None, "platform.user_signed_out", "user", user["id"], {
            "username": user["username"], **({"operator": operator} if operator else {})})
    return {"id": user["id"], "username": user["username"], "org_code": org["code"], "sessions_revoked": True}


# ---------------------------------------------------------- audit trail

def activity(
    db: Db,
    *,
    code: str | None = None,
    action: str | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> dict:
    """The audit trail (sensitive actions: accounts, fees, marks, schools…)
    across every school, newest first. Page with `before_id` =
    the previous page's `next_before_id`."""
    where: list[str] = []
    params: list[Any] = []
    if code:
        where.append("a.org_id = %s")
        params.append(_org(db, code)["id"])
    if action:
        where.append("a.action LIKE %s")
        params.append(_like(action.strip()[:60])[1:])  # prefix match: "fee" → fee.create, fee_claim.approve…
    if before_id:
        where.append("a.id < %s")
        params.append(before_id)
    clause = ("WHERE " + " AND ".join(where) + " ") if where else ""
    rows = db.all(
        "SELECT a.id, a.org_id, o.code AS org_code, o.name AS org_name, a.actor_id, "
        "u.username AS actor_username, u.full_name AS actor_name, u.user_type AS actor_type, "
        "a.action, a.entity_type, a.entity_id, a.details, a.ip, a.created_at "
        "FROM audit_logs a LEFT JOIN organisations o ON o.id = a.org_id LEFT JOIN users u ON u.id = a.actor_id "
        f"{clause}ORDER BY a.id DESC LIMIT %s",
        [*params, limit + 1],
    )
    more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for r in rows:
        details = r["details"]
        if isinstance(details, (str, bytes)):
            try:
                details = json.loads(details)
            except ValueError:
                details = {"raw": details if isinstance(details, str) else details.decode(errors="replace")}
        items.append({
            "id": r["id"],
            "at": _school_iso(r["created_at"]),
            "action": r["action"],
            "entity_type": r["entity_type"],
            "entity_id": r["entity_id"],
            "details": details,
            "ip": r["ip"],
            "organisation": {"id": r["org_id"], "code": r["org_code"], "name": r["org_name"]}
            if r["org_id"] is not None else None,
            "actor": {"id": r["actor_id"], "username": r["actor_username"], "full_name": r["actor_name"],
                      "user_type": r["actor_type"]} if r["actor_id"] is not None else None,
        })
    return {"items": items, "next_before_id": rows[-1]["id"] if more and rows else None}


# --------------------------------------------------------------- system

def system_status(db: Db) -> dict:
    """Database health and the settings that change behaviour (no secrets)."""
    started = time.perf_counter()
    db.scalar("SELECT 1")
    ping_ms = round((time.perf_counter() - started) * 1000, 1)
    info = db.one("SELECT VERSION() AS version, DATABASE() AS name") or {}
    try:
        migration = db.scalar("SELECT version_num FROM alembic_version")
    except Exception:  # noqa: BLE001 - a database set up without Alembic
        migration = None
    tables = db.all(
        "SELECT table_name AS name, COALESCE(table_rows, 0) AS row_estimate, "
        "COALESCE(data_length, 0) + COALESCE(index_length, 0) AS bytes "
        "FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY bytes DESC")
    try:
        jobs = background.queue_status(db)
    except Exception:  # noqa: BLE001 - before migration 0016
        jobs = None
    return {
        "server_time": school_now().isoformat(timespec="seconds"),
        "timezone": settings.timezone,
        "env": settings.env,
        "jobs": jobs,
        "database": {
            "ping_ms": ping_ms,
            "version": info.get("version"),
            "name": info.get("name"),
            "migration": migration,
            "size_bytes": sum(_int(t["bytes"]) for t in tables),
            "tables": [{"name": t["name"], "rows": _int(t["row_estimate"]), "bytes": _int(t["bytes"])}
                       for t in tables[:15]],
        },
        "settings": {
            "default_org_code": settings.default_org_code or None,
            "storage_backend": settings.storage_backend,
            "email_backend": settings.email_backend,
            "push_enabled": bool(settings.firebase_credentials),
            "jobs_enabled": bool(settings.jobs_secret),
            "sentry_enabled": bool(settings.sentry_dsn),
            "enforce_password_change": settings.enforce_password_change,
            "access_token_minutes": settings.access_token_minutes,
            "docs_enabled": settings.enable_docs,
        },
    }
