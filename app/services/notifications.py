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

# A phone holds one sign-in, but brothers and sisters share it (they share a username,
# services/siblings.py): registering the phone for one child registers it for the
# other active children of that username too, so each child's pushes arrive whoever is
# signed in. At most this many accounts per phone.
MAX_ACCOUNTS_PER_DEVICE = 10


def _family(db: Db, user: CurrentUser) -> list[int]:
    """The user, plus their active brothers / sisters (students only)."""
    ids = [user.id]
    if user.user_type == "student":
        ids += [r["id"] for r in db.all(
            "SELECT id FROM users WHERE org_id = %s AND username = %s AND user_type = 'student' "
            "AND is_active = 1 AND left_on IS NULL AND id <> %s ORDER BY id LIMIT %s",
            (db.org, user.username, user.id, MAX_ACCOUNTS_PER_DEVICE - 1))]
    return ids


def register_device(db: Db, user: CurrentUser, token: str, platform: str) -> None:
    """The phone with `token` gets the pushes of `user` and of their brothers /
    sisters. Any other account it was registered for (someone else signed in on
    this phone before) stops getting pushes on it."""
    family = _family(db, user)
    marks = ",".join(["%s"] * len(family))
    with db.transaction():
        db.execute(f"DELETE FROM device_tokens WHERE token = %s AND user_id NOT IN ({marks})", (token, *family))
        db.execute_many(
            "INSERT INTO device_tokens (user_id, token, platform, session_id) VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE platform = VALUES(platform), session_id = VALUES(session_id), "
            "last_seen_at = CURRENT_TIMESTAMP",
            [(uid, token, platform, user.session_id) for uid in family],
        )


def unregister_device(db: Db, token: str) -> None:
    """Signed out on that phone: no account gets pushes on it any more."""
    db.execute("DELETE FROM device_tokens WHERE token = %s", (token,))


def forget_session_devices(db: Db, session_id: str) -> None:
    """Logout: the devices registered from that login stop receiving pushes
    (for the brothers and sisters registered with it too)."""
    db.execute("DELETE FROM device_tokens WHERE session_id = %s", (session_id,))


def move_session_devices(db: Db, old_session_id: str, new_session_id: str) -> None:
    """Switch child: the phone keeps every child's registration; they now belong
    to the new sign-in (so its later logout still removes them all)."""
    db.execute("UPDATE device_tokens SET session_id = %s WHERE session_id = %s", (new_session_id, old_session_id))


def forget_user_devices(db: Db, user_id: int) -> None:
    """Logout everywhere / marked as left: no device of this user receives pushes any
    more, and a phone signed in as them (its sessions end) stops receiving the
    brothers' and sisters' pushes it was registered for too."""
    db.execute("DELETE FROM device_tokens WHERE user_id = %s OR session_id IN "
               "(SELECT id FROM auth_sessions WHERE user_id = %s)", (user_id, user_id))
