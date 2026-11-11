"""School photo gallery (migration 0022).

The office (managers) uploads photos of the school; everyone signed in at that
school — staff and parents — can see them, newest first.
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import BadRequest, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.services import audit, pagination, storage

MAX_PHOTOS = 500  # per school

_SELECT = (
    "SELECT g.id, g.image_path, g.caption, g.created_at, u.full_name AS created_by_name "
    "FROM gallery_photos g LEFT JOIN users u ON u.id = g.created_by WHERE g.org_id = %s"
)


def list_photos(db: Db, page: pagination.Page | None = None) -> pagination.PageResult:
    return pagination.fetch(db, _SELECT, [db.org], "g.id DESC", page)


def add_photo(db: Db, actor: CurrentUser, file: UploadFile, caption: str | None) -> dict:
    with db.transaction():
        # the school's row is the lock: two uploads at once can't both slip past the limit
        db.one("SELECT id FROM organisations WHERE id = %s FOR UPDATE", (db.org,))
        count = int(db.scalar("SELECT COUNT(*) FROM gallery_photos WHERE org_id = %s", (db.org,)) or 0)
        if count >= MAX_PHOTOS:
            raise BadRequest(f"The gallery can hold at most {MAX_PHOTOS} photos. Delete some older ones first.")
        stored = storage.save_upload(db, file, "gallery", storage.IMAGE_TYPES, max_mb=8,
                                     type_error="Photos must be JPEG, PNG or WebP")
        photo_id = db.execute(
            "INSERT INTO gallery_photos (org_id, image_path, caption, created_by) VALUES (%s, %s, %s, %s)",
            (db.org, stored.path, (caption or "").strip()[:200] or None, actor.id),
        ).lastrowid
        audit.record(db, actor, "gallery.add", "gallery_photo", photo_id)
    return db.one(_SELECT + " AND g.id = %s", (db.org, photo_id))


def delete_photo(db: Db, actor: CurrentUser, photo_id: int) -> None:
    with db.transaction():
        row = db.one("SELECT image_path FROM gallery_photos WHERE id = %s AND org_id = %s FOR UPDATE",
                     (photo_id, db.org))
        if not row:
            raise NotFound("Photo not found")
        db.execute("DELETE FROM gallery_photos WHERE id = %s", (photo_id,))
        storage.delete_after_commit(db, row["image_path"])
        audit.record(db, actor, "gallery.delete", "gallery_photo", photo_id)
