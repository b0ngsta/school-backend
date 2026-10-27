"""In-app notifications and push messages (FCM).

    notify(db, user_ids, "fee", "New fee: Term 2 — ₹12,500 due 30 Jun", data=routes.fee_student())

writes one `notifications` row per recipient inside the caller's transaction
(one multi-row INSERT, however many recipients) and stores the push messages
as durable background jobs in the same transaction: they are sent after the
commit, retried if FCM fails, and survive a restart — a failed push never rolls
back the action, and a rolled-back action never sends a push.

Push needs FIREBASE_CREDENTIALS (service-account JSON: a file path or the JSON
itself) and the `firebase-admin` package; without them only the in-app inbox
is filled.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.db import Db, pool
from app.services import background

log = logging.getLogger(__name__)

FCM_BATCH = 500  # FCM accepts at most 500 messages per call
_INVALID_TOKEN_ERRORS = ("UnregisteredError", "InvalidArgumentError")


@dataclass(frozen=True)
class Item:
    """One notification for one recipient."""

    user_id: int
    title: str
    body: str | None = None
    data: dict | None = None


# ------------------------------------------------------------------ sending

def notify(db: Db, user_ids: Iterable[int], type: str, title: str, body: str | None = None,
           data: dict | None = None, push: bool = True) -> None:
    """The same notification for every user in `user_ids`."""
    notify_each(db, [Item(uid, title, body, data) for uid in dict.fromkeys(user_ids)], type, push=push)


def notify_each(db: Db, items: Sequence[Item], type: str, push: bool = True) -> None:
    """A personalised notification per recipient (e.g. each child's attendance)."""
    items = [i for i in items if i.user_id]
    if not items:
        return
    db.execute_many(
        "INSERT INTO notifications (user_id, type, title, body, data) VALUES (%s, %s, %s, %s, %s)",
        [(i.user_id, type, i.title[:200], i.body, json.dumps(i.data) if i.data else None) for i in items],
    )
    if not push or not push_configured():
        return
    payload = [(i.user_id, type, i.title[:200], i.body, i.data or {}) for i in items]
    # durable jobs, stored in the caller's transaction: sent only if it commits, and
    # never lost to a restart (services/background.py); one job per FCM batch
    for start in range(0, len(payload), FCM_BATCH):
        background.enqueue(db, _send_push, payload[start:start + FCM_BATCH])


# ------------------------------------------------------------------- push

_app = None
_app_lock = threading.Lock()


def push_configured() -> bool:
    return bool(settings.firebase_credentials)


def _firebase_app():
    """The Firebase app, initialised on first use (None if push is unavailable)."""
    global _app
    if _app is not None:
        return _app
    with _app_lock:
        if _app is None:
            try:
                import firebase_admin
                from firebase_admin import credentials
            except ImportError:
                log.warning("FIREBASE_CREDENTIALS is set but firebase-admin is not installed; push is off")
                return None
            raw = settings.firebase_credentials
            info = json.loads(raw) if raw.lstrip().startswith("{") else raw
            if isinstance(info, str) and not os.path.exists(info):
                log.error("FIREBASE_CREDENTIALS file not found: %s", info)
                return None
            _app = firebase_admin.initialize_app(credentials.Certificate(info), name="school-push")
    return _app


def _message(token: str, type: str, title: str, body: str | None, data: dict):
    from firebase_admin import messaging

    fcm_data = {"type": type, "route": str(data.get("route") or ""),
                "params": json.dumps(data.get("params") or {})}
    if data.get("web_path"):
        fcm_data["web_path"] = str(data["web_path"])
    return messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body or None),
        data=fcm_data,
        android=messaging.AndroidConfig(
            priority="high", notification=messaging.AndroidNotification(channel_id="default")
        ),
    )


@background.task
def _send_push(payload: list[tuple[int, str, str, str | None, dict]]) -> None:
    """Background job: send every recipient's devices their message and drop
    the tokens FCM says are no longer valid."""
    app = _firebase_app()
    if app is None:
        return
    from firebase_admin import messaging

    user_ids = sorted({p[0] for p in payload})
    db = Db(pool)
    try:
        placeholders = ",".join(["%s"] * len(user_ids))
        rows = db.all(f"SELECT user_id, token FROM device_tokens WHERE user_id IN ({placeholders})", user_ids)
        tokens_by_user: dict[int, list[str]] = {}
        for r in rows:
            tokens_by_user.setdefault(r["user_id"], []).append(r["token"])

        outgoing = [(token, _message(token, kind, title, body, data))
                    for user_id, kind, title, body, data in payload
                    for token in tokens_by_user.get(user_id, ())]
        invalid: list[str] = []
        for start in range(0, len(outgoing), FCM_BATCH):
            chunk = outgoing[start:start + FCM_BATCH]
            result = messaging.send_each([m for _, m in chunk], app=app)
            for (token, _), response in zip(chunk, result.responses, strict=True):
                if not response.success and type(response.exception).__name__ in _INVALID_TOKEN_ERRORS:
                    invalid.append(token)
        if invalid:
            db.execute_many("DELETE FROM device_tokens WHERE token = %s", [(t,) for t in invalid])
    finally:
        db.close()


# ------------------------------------------------- texts used in messages

def day_month(d: date) -> str:
    """12 May"""
    return f"{d.day} {d:%b}"


def weekday_date(d: date) -> str:
    """Tue 12 May"""
    return f"{d:%a} {d.day} {d:%b}"


def date_range(start: date, end: date) -> str:
    """12–14 May · 30 Apr–2 May · 12 May"""
    if start == end:
        return day_month(start)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {end:%b}"
    return f"{day_month(start)}–{day_month(end)}"


def inr(amount: Any) -> str:
    """₹12,500 · ₹1,25,000 · ₹950.50 (Indian digit grouping)."""
    value = Decimal(str(amount or 0)).quantize(Decimal("0.01"))
    rupees, paise = divmod(abs(value), 1)
    digits = str(int(rupees))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    text = f"₹{digits}" + (f".{int(paise * 100):02d}" if paise else "")
    return f"-{text}" if value < 0 else text


def first_name(full_name: str | None) -> str:
    return (full_name or "").strip().split(" ")[0] or (full_name or "")


# --------------------------------------------- where tapping a message goes

STAFF_ROUTES = {"fee": "FeesAdmin", "notice": "StaffNotices", "leave": "Leaves"}
STUDENT_ROUTES = {"attendance": "Attendance", "fee": "Fees", "notice": "Notices", "homework": "Homework",
                  "timetable": "StudentTimetable", "syllabus": "Syllabus", "message": "Messages", "event": "Events"}
WEB_PATHS = {"fee": "/fees", "notice": "/notices", "leave": "/leaves"}


def route(kind: str, *, student: bool) -> dict:
    """`data` for the simple types (attendance, fee, notice, homework, leave;
    students also timetable, syllabus, message, event)."""
    data: dict = {"route": (STUDENT_ROUTES if student else STAFF_ROUTES)[kind]}
    if kind in WEB_PATHS:
        data["web_path"] = WEB_PATHS[kind]
    return data


def substitution_route(request_id: int, tab: str | None) -> dict:
    """`data` for substitution messages; `tab` is the recipient's side
    (incoming = asked to cover, outgoing = asked someone)."""
    if tab == "outgoing":
        return {"route": "Substitutions", "params": {"tab": "outgoing", "id": request_id},
                "web_path": f"/substitutions?tab=outgoing&id={request_id}"}
    if tab == "incoming":
        return {"route": "Substitutions", "params": {"tab": "incoming", "id": request_id},
                "web_path": f"/substitutions?id={request_id}"}
    return {"route": "Substitutions", "params": {"id": request_id}, "web_path": f"/substitutions?id={request_id}"}


def manager_ids(db: Db, exclude: int | None = None) -> list[int]:
    from app.core.roles import MANAGERS

    placeholders = ",".join(["%s"] * len(MANAGERS))
    rows = db.all(f"SELECT id FROM users WHERE org_id = %s AND is_active = 1 AND user_type IN ({placeholders})",
                  (db.org, *MANAGERS))
    return [r["id"] for r in rows if r["id"] != exclude]
