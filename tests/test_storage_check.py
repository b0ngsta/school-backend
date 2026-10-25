"""Storage health tools: `check-storage`, `missing-uploads`, and the warning
for hosts whose disk is wiped (Render)."""
import logging

from helpers import bearer, png_bytes

from app.cli import check_storage, find_missing_uploads
from app.core.config import ephemeral_disk, load_settings
from app.core.file_urls import relative_upload_path
from app.storage import get_store


def test_check_storage_round_trip_leaves_nothing_behind(capsys):
    check_storage()
    assert "Storage OK: local" in capsys.readouterr().out
    assert not [k for k in get_store().iter_keys() if k.startswith("_storage_check/")]


def test_missing_uploads_lists_rows_whose_file_is_gone(school, db):
    r = school.client.post(f"/users/{school.t1_id}/photo", headers=bearer(school.admin),
                           files={"file": ("me.png", png_bytes(), "image/png")})
    assert r.status_code == 200
    store = get_store()
    checked, missing = find_missing_uploads(db, store)
    assert checked >= 1 and missing == []

    path = db.scalar("SELECT photo_path FROM users WHERE id = %s", (school.t1_id,))
    store.delete(relative_upload_path(path))        # what a disk wipe does
    _, missing = find_missing_uploads(db, store)
    assert [(m["table"], m["column"], m["id"], m["school"]) for m in missing] == [
        ("users", "photo_path", school.t1_id, "DEFAULT")]


def test_render_with_local_storage_warns_at_startup(monkeypatch, caplog):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    assert ephemeral_disk()
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        load_settings()
    assert any("WILL disappear" in rec.getMessage() for rec in caplog.records)

    caplog.clear()
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "school-uploads")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        load_settings()
    assert not any("WILL disappear" in rec.getMessage() for rec in caplog.records)
