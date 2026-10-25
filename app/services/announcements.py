"""App-launch announcements: what the school wants every parent to see when
the app opens (changed school timings, an educational tour, emergencies).

Managers create them (title, message, up to MAX_IMAGES photos, optional
show-from / show-until dates, on/off). The student app shows the active ones
in a popup at every launch (`active_for_students`).
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.clock import school_today
from app.core.errors import BadRequest, NotFound
from app.db import Db
from app.models.announcements import AnnouncementSave
from app.models.auth import CurrentUser
from app.services import audit, pagination, storage

MAX_IMAGES = 5
MAX_ACTIVE_SHOWN = 10

_SELECT = (
    "SELECT a.id, a.title, a.body, a.starts_on, a.ends_on, a.is_active, a.created_at, a.updated_at, "
    "u.full_name AS created_by_name FROM announcements a LEFT JOIN users u ON u.id = a.created_by "
    "WHERE a.org_id = %s"
)


def _with_images(db: Db, rows: list[dict]) -> list[dict]:
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    placeholders = ",".join(["%s"] * len(ids))
    images: dict[int, list[dict]] = {i: [] for i in ids}
    for img in db.all(f"SELECT id, announcement_id, image_path FROM announcement_images "
                      f"WHERE announcement_id IN ({placeholders}) ORDER BY sort, id", ids):
        images[img.pop("announcement_id")].append(img)
    for r in rows:
        r["images"] = images[r["id"]]
    return rows


def _get(db: Db, announcement_id: int, lock: bool = False) -> dict:
    if lock:  # lock only the announcement row (not the joined user)
        db.one("SELECT id FROM announcements WHERE id = %s AND org_id = %s FOR UPDATE", (announcement_id, db.org))
    row = db.one(_SELECT + " AND a.id = %s", (db.org, announcement_id))
    if not row:
        raise NotFound("Announcement not found")
    return row


def list_all(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    result = pagination.fetch(db, _SELECT, (db.org,), "a.created_at DESC, a.id DESC", page)
    return pagination.PageResult(_with_images(db, result.rows), result.total)


def active_for_students(db: Db) -> list[dict]:
    today = school_today()
    rows = db.all(
        "SELECT a.id, a.title, a.body, a.starts_on, a.ends_on, a.created_at FROM announcements a "
        "WHERE a.org_id = %s AND a.is_active = 1 AND (a.starts_on IS NULL OR a.starts_on <= %s) "
        "AND (a.ends_on IS NULL OR a.ends_on >= %s) ORDER BY a.created_at DESC, a.id DESC LIMIT %s",
        (db.org, today, today, MAX_ACTIVE_SHOWN),
    )
    return _with_images(db, rows)


def create(db: Db, actor: CurrentUser, body: AnnouncementSave) -> dict:
    with db.transaction():
        announcement_id = db.execute(
            "INSERT INTO announcements (org_id, title, body, starts_on, ends_on, is_active, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (db.org, body.title.strip(), (body.body or "").strip() or None, body.starts_on, body.ends_on,
             int(body.is_active), actor.id),
        ).lastrowid
        audit.record(db, actor, "announcement.create", "announcement", announcement_id)
    return get(db, announcement_id)


def get(db: Db, announcement_id: int) -> dict:
    return _with_images(db, [_get(db, announcement_id)])[0]


def update(db: Db, actor: CurrentUser, announcement_id: int, body: AnnouncementSave) -> dict:
    with db.transaction():
        _get(db, announcement_id, lock=True)
        db.execute(
            "UPDATE announcements SET title = %s, body = %s, starts_on = %s, ends_on = %s, is_active = %s "
            "WHERE id = %s",
            (body.title.strip(), (body.body or "").strip() or None, body.starts_on, body.ends_on,
             int(body.is_active), announcement_id),
        )
        audit.record(db, actor, "announcement.update", "announcement", announcement_id)
    return get(db, announcement_id)


def delete(db: Db, actor: CurrentUser, announcement_id: int) -> None:
    with db.transaction():
        _get(db, announcement_id, lock=True)
        paths = [r["image_path"] for r in db.all(
            "SELECT image_path FROM announcement_images WHERE announcement_id = %s", (announcement_id,))]
        db.execute("DELETE FROM announcements WHERE id = %s", (announcement_id,))
        for path in paths:
            storage.delete_after_commit(db, path)
        audit.record(db, actor, "announcement.delete", "announcement", announcement_id)


def add_image(db: Db, actor: CurrentUser, announcement_id: int, file: UploadFile) -> dict:
    with db.transaction():
        _get(db, announcement_id, lock=True)
        count = int(db.scalar("SELECT COUNT(*) FROM announcement_images WHERE announcement_id = %s",
                              (announcement_id,)) or 0)
        if count >= MAX_IMAGES:
            raise BadRequest(f"An announcement can have at most {MAX_IMAGES} images")
        stored = storage.save_upload(db, file, "announcements", storage.IMAGE_TYPES, max_mb=8,
                                     type_error="Images must be JPEG, PNG or WebP")
        image_id = db.execute(
            "INSERT INTO announcement_images (announcement_id, image_path, sort) VALUES (%s, %s, %s)",
            (announcement_id, stored.path, count),
        ).lastrowid
    return {"id": image_id, "image_path": stored.path}


def delete_image(db: Db, actor: CurrentUser, announcement_id: int, image_id: int) -> None:
    with db.transaction():
        _get(db, announcement_id, lock=True)
        image = db.one("SELECT image_path FROM announcement_images WHERE id = %s AND announcement_id = %s",
                       (image_id, announcement_id))
        if not image:
            raise NotFound("Image not found")
        db.execute("DELETE FROM announcement_images WHERE id = %s", (image_id,))
        storage.delete_after_commit(db, image["image_path"])
