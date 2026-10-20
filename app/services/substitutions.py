"""Substitute teachers for the periods of a teacher on leave.

A teacher going on leave sees, per date and per period they teach, which
colleagues are free, and asks one or more of them. The colleague accepts or
declines; an accepted request can be undone ("withdrawn") until the period
starts; the requester (or a manager) can cancel. An accepted request puts the
period in the substitute's week and marks it covered in the requester's.

Statuses: pending → accepted | declined | cancelled | filled;
          accepted → withdrawn | cancelled.

The UNIQUE keys on substitution_requests (see migration 0007) are the final
guard against two people taking the same period at the same moment.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.core.clock import school_now, school_today
from app.core.errors import AppError, BadRequest, Conflict, DuplicateKey, Forbidden, NotFound
from app.core.roles import MANAGERS, STAFF
from app.db import Db
from app.models.auth import CurrentUser
from app.services import notify, pagination
from app.services.school_timings import period_start, period_times

MAX_RANGE_DAYS = 31
SUNDAY = 6
OPEN = ("pending", "accepted")

SUB_SELECT = (
    "SELECT r.id, r.leave_id, r.requester_id, r.substitute_id, r.date, r.period, r.timetable_slot_id, "
    "r.class_id, r.section_id, r.subject, r.status, r.note, r.response_note, r.responded_at, "
    "r.created_at, r.updated_at, c.name AS class_name, s.name AS section_name, "
    "req.full_name AS requester_name, req.photo_path AS requester_photo, "
    "sub.full_name AS substitute_name, sub.photo_path AS substitute_photo "
    "FROM substitution_requests r "
    "JOIN classes c ON c.id = r.class_id "
    "JOIN sections s ON s.id = r.section_id "
    "JOIN users req ON req.id = r.requester_id "
    "JOIN users sub ON sub.id = r.substitute_id"
)


# ------------------------------------------------------------------ shape

def _shape(row: dict, times: dict[int, dict]) -> dict:
    entry = times.get(row["period"]) or {}
    return {
        "id": row["id"], "leave_id": row["leave_id"],
        "date": row["date"], "day": row["date"].weekday(), "period": row["period"],
        "start_time": entry.get("start"), "end_time": entry.get("end"),
        "class_id": row["class_id"], "section_id": row["section_id"],
        "class_name": row["class_name"], "section_name": row["section_name"], "subject": row["subject"],
        "requester_id": row["requester_id"], "requester_name": row["requester_name"],
        "requester_photo": row["requester_photo"],
        "substitute_id": row["substitute_id"], "substitute_name": row["substitute_name"],
        "substitute_photo": row["substitute_photo"],
        "status": row["status"], "note": row["note"], "response_note": row["response_note"],
        "responded_at": row["responded_at"], "created_at": row["created_at"],
        "can_withdraw": _can_withdraw(row, times),
    }


def _can_withdraw(row: dict, times: dict[int, dict]) -> bool:
    """Accepted, and the period hasn't started yet (or, without school
    timings, the day hasn't passed)."""
    if row["status"] != "accepted":
        return False
    starts = period_start(row["date"], row["period"], times)
    if starts is None:
        return row["date"] >= school_today()
    return school_now() < starts


def get_shaped(db: Db, request_id: int, times: dict[int, dict] | None = None) -> dict | None:
    row = db.one(SUB_SELECT + " WHERE r.id = %s", (request_id,))
    return _shape(row, period_times(db) if times is None else times) if row else None


def for_leave(db: Db, leave_id: int) -> list[dict]:
    times = period_times(db)
    rows = db.all(SUB_SELECT + " WHERE r.leave_id = %s ORDER BY r.date, r.period, r.created_at DESC, r.id DESC",
                  (leave_id,))
    return [_shape(r, times) for r in rows]


# ---------------------------------------------------------------- helpers

def _requester_id(user: CurrentUser, teacher_id: int | None) -> int:
    """Teachers plan for themselves; managers may plan for any teacher."""
    if teacher_id is None or teacher_id == user.id:
        return user.id
    if user.user_type not in MANAGERS:
        raise Forbidden("You can only plan substitutes for your own classes")
    return teacher_id


def _period_label(row: dict, times: dict[int, dict]) -> str:
    """Tue 12 May · 10:20–11:05 · Class 5-B Mathematics"""
    parts = [notify.weekday_date(row["date"])]
    entry = times.get(row["period"])
    if entry:
        parts.append(f"{entry['start']}–{entry['end']}")
    parts.append(" ".join(p for p in (f"{row['class_name']}-{row['section_name']}", row.get("subject")) if p))
    return " · ".join(parts)


def _busy_reason(db: Db, teacher_id: int, day: date, period: int) -> str | None:
    """Why a teacher can't cover `period` on `day` (None = free)."""
    if db.one("SELECT 1 FROM timetable_slots WHERE teacher_id = %s AND day = %s AND period = %s LIMIT 1",
              (teacher_id, day.weekday(), period)):
        return "has a class"
    if db.one("SELECT 1 FROM substitution_requests WHERE substitute_id = %s AND date = %s AND period = %s "
              "AND status = 'accepted' LIMIT 1", (teacher_id, day, period)):
        return "is already covering"
    if db.one("SELECT 1 FROM leave_requests WHERE user_id = %s AND status IN ('pending', 'approved') "
              "AND from_date <= %s AND to_date >= %s LIMIT 1", (teacher_id, day, day)):
        return "is on leave"
    return None


def _holiday(db: Db, day: date) -> str | None:
    return db.scalar("SELECT name FROM holidays WHERE date = %s", (day,))


def _lock_users(db: Db, *user_ids: int) -> None:
    """Serialise accept/withdraw for the same people (always in id order, so no deadlocks)."""
    ids = sorted(set(user_ids))
    db.all(f"SELECT id FROM users WHERE id IN ({','.join(['%s'] * len(ids))}) ORDER BY id FOR UPDATE", ids)


# ----------------------------------------------------------- availability

def availability(db: Db, user: CurrentUser, from_date: date, to_date: date, leave_id: int | None,
                 teacher_id: int | None) -> dict:
    requester_id = _requester_id(user, teacher_id)
    if to_date < from_date:
        raise BadRequest("'to' must be on or after 'from'")
    if (to_date - from_date).days + 1 > MAX_RANGE_DAYS:
        raise BadRequest(f"Choose at most {MAX_RANGE_DAYS} days at a time")
    if leave_id is not None and not db.one("SELECT id FROM leave_requests WHERE id = %s AND user_id = %s",
                                           (leave_id, requester_id)):
        raise NotFound("Leave request not found")

    times = period_times(db)
    dates = [from_date + timedelta(days=i) for i in range((to_date - from_date).days + 1)]
    dates = [d for d in dates if d.weekday() != SUNDAY]
    holidays = {r["date"]: r["name"] for r in db.all(
        "SELECT date, name FROM holidays WHERE date BETWEEN %s AND %s", (from_date, to_date))}
    weekdays = sorted({d.weekday() for d in dates})

    my_slots: dict[int, list[dict]] = defaultdict(list)
    for s in db.all(
        "SELECT t.id, t.day, t.period, t.class_id, t.section_id, t.subject, "
        "c.name AS class_name, s.name AS section_name FROM timetable_slots t "
        "JOIN classes c ON c.id = t.class_id JOIN sections s ON s.id = t.section_id "
        "WHERE t.teacher_id = %s ORDER BY t.day, t.period",
        (requester_id,),
    ):
        my_slots[s["day"]].append(s)

    teachers = db.all(
        "SELECT u.id AS teacher_id, u.full_name, u.photo_path, tp.subject FROM users u "
        "LEFT JOIN teacher_profiles tp ON tp.user_id = u.id "
        "WHERE u.user_type = 'teacher' AND u.is_active = 1 AND u.id != %s",
        (requester_id,),
    )
    busy_slots: set[tuple[int, int, int]] = set()
    if weekdays:
        busy_slots = {(r["teacher_id"], r["day"], r["period"]) for r in db.all(
            f"SELECT teacher_id, day, period FROM timetable_slots WHERE day IN ({','.join(['%s'] * len(weekdays))})",
            weekdays)}

    week_start = from_date - timedelta(days=from_date.weekday())
    week_end = to_date + timedelta(days=SUNDAY - to_date.weekday())
    covering: set[tuple[int, date, int]] = set()
    per_week: dict[tuple[int, date], int] = defaultdict(int)
    for r in db.all(
        "SELECT substitute_id, date, period FROM substitution_requests "
        "WHERE status = 'accepted' AND date BETWEEN %s AND %s",
        (week_start, week_end),
    ):
        covering.add((r["substitute_id"], r["date"], r["period"]))
        per_week[(r["substitute_id"], r["date"] - timedelta(days=r["date"].weekday()))] += 1

    on_leave: dict[int, list[tuple[date, date]]] = defaultdict(list)
    for r in db.all(
        "SELECT user_id, from_date, to_date FROM leave_requests WHERE status IN ('pending', 'approved') "
        "AND from_date <= %s AND to_date >= %s",
        (to_date, from_date),
    ):
        on_leave[r["user_id"]].append((r["from_date"], r["to_date"]))

    requests: dict[tuple[date, int], list[dict]] = defaultdict(list)
    for r in db.all(SUB_SELECT + " WHERE r.requester_id = %s AND r.date BETWEEN %s AND %s "
                    "ORDER BY r.created_at DESC, r.id DESC", (requester_id, from_date, to_date)):
        requests[(r["date"], r["period"])].append(_shape(r, times))

    def free(teacher: dict, d: date, period: int) -> bool:
        tid = teacher["teacher_id"]
        return ((tid, d.weekday(), period) not in busy_slots
                and (tid, d, period) not in covering
                and not any(start <= d <= end for start, end in on_leave.get(tid, ())))

    def same_subject(teacher: dict, subject: str | None) -> bool:
        if not subject or not teacher["subject"]:
            return False
        wanted = subject.strip().lower()
        return any(part.strip().lower() == wanted for part in teacher["subject"].split(","))

    days = []
    for d in dates:
        holiday = holidays.get(d)
        periods = []
        if not holiday:
            monday = d - timedelta(days=d.weekday())
            for slot in my_slots.get(d.weekday(), []):
                entry = times.get(slot["period"]) or {}
                available = [t for t in teachers if free(t, d, slot["period"])]
                available.sort(key=lambda t: (not same_subject(t, slot["subject"]),
                                              per_week.get((t["teacher_id"], monday), 0),
                                              (t["full_name"] or "").lower(), t["teacher_id"]))
                periods.append({
                    "period": slot["period"], "start_time": entry.get("start"), "end_time": entry.get("end"),
                    "slot": {"id": slot["id"], "class_id": slot["class_id"], "section_id": slot["section_id"],
                             "class_name": slot["class_name"], "section_name": slot["section_name"],
                             "subject": slot["subject"]},
                    "requests": requests.get((d, slot["period"]), []),
                    "available": [{"teacher_id": t["teacher_id"], "full_name": t["full_name"],
                                   "photo_path": t["photo_path"], "subject": t["subject"]} for t in available],
                })
        days.append({"date": d, "day": d.weekday(), "holiday": holiday, "periods": periods})
    return {"days": days}


# ----------------------------------------------------------------- create

def create(db: Db, requester: dict, day: date, period: int, substitute_id: int, leave_id: int | None = None,
           note: str | None = None, *, actor_is_requester: bool = True) -> int:
    """Create one request (rules in BACKEND_README §2.5). Call inside a
    transaction; `requester` is {"id", "full_name"}. Returns the new id."""
    who = "You have" if actor_is_requester else f"{requester['full_name']} has"
    when = notify.weekday_date(day)
    if day < school_today():
        raise BadRequest(f"{when} is in the past")
    if day.weekday() == SUNDAY:
        raise BadRequest(f"{when} is a Sunday")
    holiday = _holiday(db, day)
    if holiday:
        raise BadRequest(f"{when} is a holiday ({holiday})")

    slot = db.one(
        "SELECT t.id, t.class_id, t.section_id, t.subject, c.name AS class_name, s.name AS section_name "
        "FROM timetable_slots t JOIN classes c ON c.id = t.class_id JOIN sections s ON s.id = t.section_id "
        "WHERE t.teacher_id = %s AND t.day = %s AND t.period = %s",
        (requester["id"], day.weekday(), period),
    )
    if not slot:
        raise BadRequest(f"{who} no class in period {period} on {when}")

    substitute = db.one("SELECT id, full_name FROM users WHERE id = %s AND user_type = 'teacher' AND is_active = 1",
                        (substitute_id,))
    if not substitute:
        raise NotFound("Substitute teacher not found")
    if substitute["id"] == requester["id"]:
        raise BadRequest("A teacher can't be their own substitute")
    if _busy_reason(db, substitute["id"], day, period):
        raise Conflict(f"{substitute['full_name']} is not free in period {period} that day")

    covered = db.one(
        "SELECT u.full_name FROM substitution_requests r JOIN users u ON u.id = r.substitute_id "
        "WHERE r.requester_id = %s AND r.date = %s AND r.period = %s AND r.status = 'accepted'",
        (requester["id"], day, period),
    )
    if covered:
        raise Conflict(f"Period {period} is already covered by {covered['full_name']}")
    duplicate = f"{substitute['full_name']} has already been asked for period {period} on {when}"
    if db.one("SELECT id FROM substitution_requests WHERE requester_id = %s AND substitute_id = %s "
              "AND date = %s AND period = %s AND status IN ('pending', 'accepted')",
              (requester["id"], substitute["id"], day, period)):
        raise Conflict(duplicate)

    if leave_id is not None:
        leave = db.one("SELECT * FROM leave_requests WHERE id = %s AND user_id = %s", (leave_id, requester["id"]))
        if not leave:
            raise NotFound("Leave request not found")
        if leave["status"] in ("rejected", "cancelled"):
            raise Conflict(f"That leave request was {leave['status']}")
        if not leave["from_date"] <= day <= leave["to_date"]:
            raise BadRequest(f"{when} is not inside that leave")

    try:
        request_id = db.execute(
            "INSERT INTO substitution_requests (leave_id, requester_id, substitute_id, date, period, "
            "timetable_slot_id, class_id, section_id, subject, note) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (leave_id, requester["id"], substitute["id"], day, period, slot["id"], slot["class_id"],
             slot["section_id"], slot["subject"], (note or "").strip() or None),
        ).lastrowid
    except DuplicateKey:
        raise Conflict(duplicate) from None

    times = period_times(db)
    label = _period_label({"date": day, "period": period, **slot}, times)
    notify.notify(db, [substitute["id"]], "substitution_request",
                  f"{requester['full_name']} asks you to take Period {period}", label,
                  data=notify.substitution_route(request_id, "incoming"))
    return request_id


def create_request(db: Db, user: CurrentUser, body) -> dict:
    requester_id = _requester_id(user, body.teacher_id)
    with db.transaction():
        requester = db.one("SELECT id, full_name FROM users WHERE id = %s AND is_active = 1 AND user_type IN "
                           f"({','.join(['%s'] * len(STAFF))})", (requester_id, *STAFF))
        if not requester:
            raise NotFound("Teacher not found")
        request_id = create(db, requester, body.date, body.period, body.substitute_id, body.leave_id, body.note,
                            actor_is_requester=requester_id == user.id)
    return get_shaped(db, request_id)


def create_many(db: Db, requester: dict, items, leave_id: int) -> None:
    """For POST/PUT /leaves: every item or none (the caller's transaction rolls
    back). Any rule failing becomes 409 with the reason."""
    for item in items:
        try:
            create(db, requester, item.date, item.period, item.substitute_id, leave_id)
        except AppError as exc:
            if isinstance(exc, Conflict):
                raise
            raise Conflict(exc.detail) from None


# -------------------------------------------------------------- responses

def _load_for_update(db: Db, request_id: int) -> dict:
    row = db.one("SELECT * FROM substitution_requests WHERE id = %s FOR UPDATE", (request_id,))
    if not row:
        raise NotFound("Substitution request not found")
    return row


def _names(db: Db, row: dict) -> dict:
    return db.one(
        "SELECT req.full_name AS requester_name, sub.full_name AS substitute_name, "
        "c.name AS class_name, s.name AS section_name FROM users req, users sub, classes c, sections s "
        "WHERE req.id = %s AND sub.id = %s AND c.id = %s AND s.id = %s",
        (row["requester_id"], row["substitute_id"], row["class_id"], row["section_id"]),
    ) or {}


def _only_substitute(user: CurrentUser, row: dict) -> None:
    if row["substitute_id"] != user.id:
        if user.id == row["requester_id"] or user.user_type in MANAGERS:
            raise Forbidden("Only the teacher who was asked can answer this request")
        raise NotFound("Substitution request not found")


def accept(db: Db, user: CurrentUser, request_id: int, note: str | None) -> dict:
    peek = db.one("SELECT requester_id, substitute_id FROM substitution_requests WHERE id = %s", (request_id,))
    if not peek:
        raise NotFound("Substitution request not found")
    taken_by = None
    try:
        with db.transaction():
            _lock_users(db, peek["requester_id"], peek["substitute_id"])
            row = _load_for_update(db, request_id)
            _only_substitute(user, row)
            if row["status"] != "pending":
                raise Conflict(f"This request is already {row['status']}")
            other = db.one(
                "SELECT u.full_name FROM substitution_requests r JOIN users u ON u.id = r.substitute_id "
                "WHERE r.requester_id = %s AND r.date = %s AND r.period = %s AND r.status = 'accepted'",
                (row["requester_id"], row["date"], row["period"]),
            )
            if other:
                db.execute("UPDATE substitution_requests SET status = 'filled' WHERE id = %s", (request_id,))
                taken_by = other["full_name"]
            else:
                if _busy_reason(db, user.id, row["date"], row["period"]):
                    raise Conflict(f"You are not free in period {row['period']} that day")
                db.execute(
                    "UPDATE substitution_requests SET status = 'accepted', responded_at = NOW(), "
                    "response_note = %s WHERE id = %s",
                    ((note or "").strip() or None, request_id),
                )
                _fill_others(db, row)
                names = _names(db, row)
                notify.notify(db, [row["requester_id"]], "substitution_update",
                              f"{names.get('substitute_name')} will take Period {row['period']} on "
                              f"{notify.weekday_date(row['date'])}",
                              _period_label({**row, **names}, period_times(db)),
                              data=notify.substitution_route(request_id, "outgoing"))
    except DuplicateKey as exc:
        if exc.key == "ux_sub_req_sub_busy":
            raise Conflict("You are already covering another class in that period") from None
        raise Conflict("Someone else just took this period") from None
    if taken_by:
        raise Conflict(f"{taken_by} already took this period")
    return get_shaped(db, request_id)


def _fill_others(db: Db, row: dict) -> None:
    """The period is covered: other pending requests for it are no longer needed."""
    others = db.all(
        "SELECT id, substitute_id FROM substitution_requests WHERE requester_id = %s AND date = %s "
        "AND period = %s AND status = 'pending' AND id != %s FOR UPDATE",
        (row["requester_id"], row["date"], row["period"], row["id"]),
    )
    if not others:
        return
    ids = [o["id"] for o in others]
    db.execute(f"UPDATE substitution_requests SET status = 'filled' WHERE id IN ({','.join(['%s'] * len(ids))})",
               ids)
    substitute = db.scalar("SELECT full_name FROM users WHERE id = %s", (row["substitute_id"],))
    title = f"No longer needed: Period {row['period']} on {notify.weekday_date(row['date'])}"
    notify.notify_each(db, [notify.Item(o["substitute_id"], title, f"{substitute} is covering",
                                        notify.substitution_route(o["id"], "incoming")) for o in others],
                       "substitution_update")


def decline(db: Db, user: CurrentUser, request_id: int, note: str | None) -> dict:
    with db.transaction():
        row = _load_for_update(db, request_id)
        _only_substitute(user, row)
        if row["status"] != "pending":
            raise Conflict(f"This request is already {row['status']}")
        note = (note or "").strip() or None
        db.execute("UPDATE substitution_requests SET status = 'declined', responded_at = NOW(), "
                   "response_note = %s WHERE id = %s", (note, request_id))
        names = _names(db, row)
        notify.notify(db, [row["requester_id"]], "substitution_update",
                      f"{names.get('substitute_name')} can't take Period {row['period']} on "
                      f"{notify.weekday_date(row['date'])}", note,
                      data=notify.substitution_route(request_id, "outgoing"))
    return get_shaped(db, request_id)


def withdraw(db: Db, user: CurrentUser, request_id: int, note: str | None) -> dict:
    times = period_times(db)
    with db.transaction():
        row = _load_for_update(db, request_id)
        _only_substitute(user, row)
        if row["status"] != "accepted":
            raise Conflict("Only an accepted request can be withdrawn")
        if not _can_withdraw(row, times):
            raise Conflict("This period has already started")
        note = (note or "").strip() or None
        db.execute("UPDATE substitution_requests SET status = 'withdrawn', responded_at = NOW(), "
                   "response_note = %s WHERE id = %s", (note, request_id))
        names = _names(db, row)
        title = (f"{names.get('substitute_name')} can no longer take Period {row['period']} on "
                 f"{notify.weekday_date(row['date'])}")
        text = "Please find someone else" + (f" — {note}" if note else "")
        notify.notify(db, [row["requester_id"]], "substitution_update", title, text,
                      data=notify.substitution_route(request_id, "outgoing"))
        if row["date"] <= school_today() + timedelta(days=1):
            managers = [m for m in notify.manager_ids(db) if m not in (row["requester_id"], row["substitute_id"])]
            notify.notify(db, managers, "substitution_update", title,
                          f"{names.get('requester_name')}'s class {names.get('class_name')}-"
                          f"{names.get('section_name')} needs a substitute",
                          data=notify.substitution_route(request_id, None))
    return get_shaped(db, request_id, times)


def cancel(db: Db, user: CurrentUser, request_id: int, note: str | None) -> dict:
    with db.transaction():
        row = _load_for_update(db, request_id)
        if row["requester_id"] != user.id and user.user_type not in MANAGERS:
            if row["substitute_id"] == user.id:
                raise Forbidden("Use decline or withdraw for requests sent to you")
            raise NotFound("Substitution request not found")
        if row["status"] not in OPEN:
            raise Conflict(f"This request is already {row['status']}")
        db.execute("UPDATE substitution_requests SET status = 'cancelled' WHERE id = %s", (request_id,))
        _notify_cancelled(db, [row], (note or "").strip() or None)
    return get_shaped(db, request_id)


def _notify_cancelled(db: Db, rows: list[dict], note: str | None = None) -> None:
    notify.notify_each(db, [
        notify.Item(r["substitute_id"],
                    f"Request withdrawn: Period {r['period']} on {notify.weekday_date(r['date'])}", note,
                    notify.substitution_route(r["id"], "incoming"))
        for r in rows
    ], "substitution_update")


def cancel_for_leave(db: Db, leave_id: int, keep_from: date | None = None, keep_to: date | None = None) -> int:
    """The leave was deleted, rejected or shortened: cancel its open requests
    (only those outside keep_from…keep_to when given) and tell the substitutes."""
    sql = "SELECT * FROM substitution_requests WHERE leave_id = %s AND status IN ('pending', 'accepted')"
    params: list = [leave_id]
    if keep_from is not None and keep_to is not None:
        sql += " AND (date < %s OR date > %s)"
        params += [keep_from, keep_to]
    rows = db.all(sql + " FOR UPDATE", params)
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    db.execute(f"UPDATE substitution_requests SET status = 'cancelled' WHERE id IN ({','.join(['%s'] * len(ids))})",
               ids)
    _notify_cancelled(db, rows)
    return len(rows)


def link_standalone(db: Db, requester_id: int, leave_id: int, from_date: date, to_date: date) -> None:
    """Requests sent before the leave existed (leave_id NULL) belong to it now."""
    db.execute(
        "UPDATE substitution_requests SET leave_id = %s WHERE requester_id = %s AND leave_id IS NULL "
        "AND status IN ('pending', 'accepted') AND date BETWEEN %s AND %s",
        (leave_id, requester_id, from_date, to_date),
    )


# ------------------------------------------------------------------ lists

def _list(db: Db, where: list[str], params: list, status: str | None, from_date: date | None,
          to_date: date | None, page: pagination.Page | None) -> pagination.PageResult:
    if status:
        where.append("r.status = %s")
        params.append(status)
    if from_date:
        where.append("r.date >= %s")
        params.append(from_date)
    if to_date:
        where.append("r.date <= %s")
        params.append(to_date)
    sql = SUB_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    result = pagination.fetch(db, sql, params, "r.date DESC, r.period ASC, r.id DESC", page)
    times = period_times(db)
    return pagination.PageResult([_shape(r, times) for r in result.rows], result.total)


def incoming(db: Db, user: CurrentUser, status, from_date, to_date, page) -> pagination.PageResult:
    return _list(db, ["r.substitute_id = %s"], [user.id], status, from_date, to_date, page)


def outgoing(db: Db, user: CurrentUser, status, from_date, to_date, page) -> pagination.PageResult:
    return _list(db, ["r.requester_id = %s"], [user.id], status, from_date, to_date, page)


def list_all(db: Db, status, from_date, to_date, day: date | None, teacher_id: int | None,
             page) -> pagination.PageResult:
    where, params = [], []
    if day:
        where.append("r.date = %s")
        params.append(day)
    if teacher_id:
        where.append("(r.requester_id = %s OR r.substitute_id = %s)")
        params += [teacher_id, teacher_id]
    return _list(db, where, params, status, from_date, to_date, page)


def get_one(db: Db, user: CurrentUser, request_id: int) -> dict:
    row = get_shaped(db, request_id)
    if not row or (user.user_type not in MANAGERS and user.id not in (row["requester_id"], row["substitute_id"])):
        raise NotFound("Substitution request not found")
    return row


# --------------------------------------------------------- leave coverage

def coverage(db: Db, leaves: list[dict]) -> None:
    """Adds `coverage` to each leave row: {"periods", "covered", "pending"} for
    the periods the applicant teaches inside the leave (school days, not
    holidays), or None for staff without timetable periods."""
    if not leaves:
        return
    user_ids = sorted({lv["user_id"] for lv in leaves})
    first, last = min(lv["from_date"] for lv in leaves), max(lv["to_date"] for lv in leaves)
    users_sql = ",".join(["%s"] * len(user_ids))
    slots: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for s in db.all(f"SELECT teacher_id, day, period FROM timetable_slots WHERE teacher_id IN ({users_sql})",
                    user_ids):
        slots[s["teacher_id"]][s["day"]].add(s["period"])
    holidays = {r["date"] for r in db.all("SELECT date FROM holidays WHERE date BETWEEN %s AND %s", (first, last))}
    state: dict[tuple[int, date, int], str] = {}
    for r in db.all(
        f"SELECT requester_id, date, period, status FROM substitution_requests WHERE requester_id IN ({users_sql}) "
        "AND date BETWEEN %s AND %s AND status IN ('pending', 'accepted')",
        (*user_ids, first, last),
    ):
        key = (r["requester_id"], r["date"], r["period"])
        if state.get(key) != "accepted":
            state[key] = r["status"]

    for lv in leaves:
        teaches = slots.get(lv["user_id"])
        if not teaches:
            lv["coverage"] = None
            continue
        periods = covered = pending = 0
        d = lv["from_date"]
        while d <= lv["to_date"]:
            if d.weekday() != SUNDAY and d not in holidays:
                for period in teaches.get(d.weekday(), ()):
                    periods += 1
                    status = state.get((lv["user_id"], d, period))
                    covered += status == "accepted"
                    pending += status == "pending"
            d += timedelta(days=1)
        lv["coverage"] = {"periods": periods, "covered": covered, "pending": pending}
