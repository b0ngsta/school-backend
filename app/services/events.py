"""School events (annual day, sports day, educational tour, …).

Managers create them; everyone signed in (staff and parents) can see them.
An event is "upcoming" until its end date has passed, then "completed";
completed events usually get photos (uploaded) and videos (links to
YouTube / Drive — large video files don't go through this server).
"""
from __future__ import annotations

from typing import Literal

from fastapi import UploadFile

from app.core.clock import school_today
from app.core.errors import BadRequest, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.events import EventSave, EventVideo
from app.services import audit, notify, pagination, storage, times

MAX_PHOTOS = 40
Status = Literal["upcoming", "completed"]

_SELECT = (
    "SELECT e.id, e.title, e.description, e.venue, e.start_date, e.end_date, e.start_time, e.end_time, "
    "e.created_at, e.updated_at, u.full_name AS created_by_name, "
    "CASE WHEN e.end_date < %s THEN 'completed' ELSE 'upcoming' END AS status "
    "FROM events e LEFT JOIN users u ON u.id = e.created_by WHERE e.org_id = %s"
)


def _shape(db: Db, rows: list[dict]) -> list[dict]:
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    placeholders = ",".join(["%s"] * len(ids))
    media: dict[int, list[dict]] = {i: [] for i in ids}
    for m in db.all(f"SELECT id, event_id, kind, media_path, url, caption FROM event_media "
                    f"WHERE event_id IN ({placeholders}) ORDER BY id", ids):
        media[m.pop("event_id")].append(m)
    for r in rows:
        times.format_row(r, "start_time", "end_time")
        r["media"] = media[r["id"]]
    return rows


def list_events(db: Db, status: Status | None, page: pagination.Page | None = None) -> pagination.PageResult:
    today = school_today()
    sql, params, order = _SELECT, [today, db.org], "e.start_date DESC, e.id DESC"
    if status == "upcoming":
        sql += " AND e.end_date >= %s"
        params.append(today)
        order = "e.start_date, e.start_time, e.id"
    elif status == "completed":
        sql += " AND e.end_date < %s"
        params.append(today)
        order = "e.end_date DESC, e.id DESC"
    result = pagination.fetch(db, sql, params, order, page)
    return pagination.PageResult(_shape(db, result.rows), result.total)


def _get(db: Db, event_id: int) -> dict:
    row = db.one(_SELECT + " AND e.id = %s", (school_today(), db.org, event_id))
    if not row:
        raise NotFound("Event not found")
    return row


def get(db: Db, event_id: int) -> dict:
    return _shape(db, [_get(db, event_id)])[0]


def _lock(db: Db, event_id: int) -> None:
    if not db.one("SELECT id FROM events WHERE id = %s AND org_id = %s FOR UPDATE", (event_id, db.org)):
        raise NotFound("Event not found")


def _values(body: EventSave) -> tuple:
    return (body.title.strip(), (body.description or "").strip() or None, (body.venue or "").strip() or None,
            body.start_date, body.end_date, body.start_time, body.end_time)


def create(db: Db, actor: CurrentUser, body: EventSave) -> dict:
    with db.transaction():
        event_id = db.execute(
            "INSERT INTO events (org_id, title, description, venue, start_date, end_date, start_time, end_time, "
            "created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, *_values(body), actor.id),
        ).lastrowid
        audit.record(db, actor, "event.create", "event", event_id)
        if body.end_date >= school_today():
            students = [r["id"] for r in db.all(
                "SELECT id FROM users WHERE org_id = %s AND user_type = 'student' AND is_active = 1", (db.org,))]
            when = notify.date_range(body.start_date, body.end_date)
            notify.notify(db, students, "event", f"🎉 {body.title.strip()} ({when})",
                          data=notify.route("event", student=True))
    return get(db, event_id)


def update(db: Db, actor: CurrentUser, event_id: int, body: EventSave) -> dict:
    with db.transaction():
        _lock(db, event_id)
        db.execute(
            "UPDATE events SET title = %s, description = %s, venue = %s, start_date = %s, end_date = %s, "
            "start_time = %s, end_time = %s WHERE id = %s",
            (*_values(body), event_id),
        )
        audit.record(db, actor, "event.update", "event", event_id)
    return get(db, event_id)


def delete(db: Db, actor: CurrentUser, event_id: int) -> None:
    with db.transaction():
        _lock(db, event_id)
        paths = [r["media_path"] for r in db.all(
            "SELECT media_path FROM event_media WHERE event_id = %s AND media_path IS NOT NULL", (event_id,))]
        db.execute("DELETE FROM events WHERE id = %s", (event_id,))
        for path in paths:
            storage.delete_after_commit(db, path)
        audit.record(db, actor, "event.delete", "event", event_id)


def add_photo(db: Db, actor: CurrentUser, event_id: int, file: UploadFile, caption: str | None) -> dict:
    with db.transaction():
        _lock(db, event_id)
        count = int(db.scalar("SELECT COUNT(*) FROM event_media WHERE event_id = %s AND kind = 'image'",
                              (event_id,)) or 0)
        if count >= MAX_PHOTOS:
            raise BadRequest(f"An event can have at most {MAX_PHOTOS} photos")
        stored = storage.save_upload(db, file, "events", storage.IMAGE_TYPES, max_mb=8,
                                     type_error="Photos must be JPEG, PNG or WebP")
        media_id = db.execute(
            "INSERT INTO event_media (event_id, kind, media_path, caption) VALUES (%s, 'image', %s, %s)",
            (event_id, stored.path, (caption or "").strip()[:200] or None),
        ).lastrowid
    return {"id": media_id, "kind": "image", "media_path": stored.path}


def add_video(db: Db, actor: CurrentUser, event_id: int, body: EventVideo) -> dict:
    with db.transaction():
        _lock(db, event_id)
        media_id = db.execute(
            "INSERT INTO event_media (event_id, kind, url, caption) VALUES (%s, 'video', %s, %s)",
            (event_id, body.url, (body.caption or "").strip() or None),
        ).lastrowid
    return {"id": media_id, "kind": "video", "url": body.url}


def delete_media(db: Db, actor: CurrentUser, event_id: int, media_id: int) -> None:
    with db.transaction():
        _lock(db, event_id)
        media = db.one("SELECT media_path FROM event_media WHERE id = %s AND event_id = %s", (media_id, event_id))
        if not media:
            raise NotFound("Photo or video not found")
        db.execute("DELETE FROM event_media WHERE id = %s", (media_id,))
        storage.delete_after_commit(db, media["media_path"])
