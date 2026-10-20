"""The signed-in user's notification inbox and push-device registrations."""
from __future__ import annotations

import json

from app.core.errors import NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.services import pagination

DEFAULT_LIMIT = 200  # without ?limit= the newest 200 are returned

NOTIFICATION_SELECT = (
    "SELECT id, type, title, body, data, read_at, created_at FROM notifications WHERE user_id = %s"
)


def _decode(row: dict) -> dict:
    if isinstance(row.get("data"), (str, bytes)):
        row["data"] = json.loads(row["data"])
    return row


def list_notifications(db: Db, user: CurrentUser, page: pagination.Page | None = None) -> pagination.PageResult:
    result = pagination.fetch(db, NOTIFICATION_SELECT, (user.id,), "created_at DESC, id DESC", page,
                              default_limit=DEFAULT_LIMIT)
    return pagination.PageResult([_decode(r) for r in result.rows], result.total)


def unread_count(db: Db, user: CurrentUser) -> dict:
    count = db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND read_at IS NULL", (user.id,))
    return {"count": int(count or 0)}


def mark_read(db: Db, user: CurrentUser, notification_id: int) -> None:
    row = db.one("SELECT id FROM notifications WHERE id = %s AND user_id = %s", (notification_id, user.id))
    if not row:
        raise NotFound("Notification not found")
    db.execute("UPDATE notifications SET read_at = NOW() WHERE id = %s AND read_at IS NULL", (notification_id,))


def mark_all_read(db: Db, user: CurrentUser) -> None:
    db.execute("UPDATE notifications SET read_at = NOW() WHERE user_id = %s AND read_at IS NULL", (user.id,))


# --------------------------------------------------------------- devices

def register_device(db: Db, user: CurrentUser, token: str, platform: str) -> None:
    """Upsert by token; a token that belonged to another account moves to this one."""
    db.execute(
        "INSERT INTO device_tokens (user_id, token, platform, session_id) VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), platform = VALUES(platform), "
        "session_id = VALUES(session_id), last_seen_at = CURRENT_TIMESTAMP",
        (user.id, token, platform, user.session_id),
    )


def unregister_device(db: Db, token: str) -> None:
    db.execute("DELETE FROM device_tokens WHERE token = %s", (token,))


def forget_session_devices(db: Db, session_id: str) -> None:
    """Logout: the devices registered from that login stop receiving pushes."""
    db.execute("DELETE FROM device_tokens WHERE session_id = %s", (session_id,))


def forget_user_devices(db: Db, user_id: int) -> None:
    """Logout everywhere: no device of this user receives pushes any more."""
    db.execute("DELETE FROM device_tokens WHERE user_id = %s", (user_id,))
