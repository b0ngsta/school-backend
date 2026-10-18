"""Uploaded files: validation, safe storage and serving.

Every upload is checked by its actual bytes (not the name or the type the
client claims), stored under a random name with an extension chosen by the
server, and images are re-encoded, which strips hidden metadata such as GPS
location from phone photos and neutralises crafted files.

Where the bytes live (server disk or an S3-compatible bucket) is decided by
`app.storage`; the database always stores "uploads/<folder>/<name>", so a
school can move between the two without touching any data.
"""
from __future__ import annotations

import io
import logging
import os
import secrets
import warnings
from dataclasses import dataclass

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.responses import Response

from app.core.config import settings
from app.core.errors import NotFound, PayloadTooLarge, UnsupportedMediaType
from app.core.file_urls import relative_upload_path
from app.db import Db
from app.storage import get_store

log = logging.getLogger(__name__)

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
PDF_TYPES = frozenset({"application/pdf"})

EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}
_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".pdf": "application/pdf"}
_PIL_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}

_CHUNK = 1024 * 1024
Image.MAX_IMAGE_PIXELS = 60_000_000  # decompression-bomb guard (~60 MP)


@dataclass(frozen=True)
class StoredFile:
    path: str            # what goes in the database, e.g. "uploads/photos/<hex>.jpg"
    content_type: str    # detected type
    original_name: str | None
    key: str = ""        # key inside the store, e.g. "photos/<hex>.jpg"


# ------------------------------------------------------------------ saving

def save_upload(
    db: Db,
    file: UploadFile,
    subdir: str,
    allowed_types: frozenset[str],
    max_mb: int = 10,
    type_error: str | None = None,
) -> StoredFile:
    """Validate and store an upload. Must run inside `db.transaction()`: if the
    transaction rolls back, the stored file is removed again."""
    if file.content_type not in allowed_types:
        raise UnsupportedMediaType(type_error or f"File type not allowed: {file.content_type}")
    stored = store_bytes(_read_limited(file, max_mb), subdir, allowed_types, original_name=file.filename)
    store = get_store()
    db.on_rollback(lambda: store.delete(stored.key))
    return stored


def store_bytes(data: bytes, subdir: str, allowed_types: frozenset[str],
                original_name: str | None = None) -> StoredFile:
    """Check the bytes, re-encode images and store them under a random name.
    The caller removes the file again (`delete_file`) if it ends up unused."""
    detected = _sniff(data)
    if detected not in allowed_types:
        raise UnsupportedMediaType("File content does not match an allowed file type")
    if detected in IMAGE_TYPES:
        data = _reencode_image(data, detected)

    name = f"{secrets.token_hex(16)}{EXTENSIONS[detected]}"
    key = f"{subdir}/{name}"
    get_store().put(key, data, detected)
    path = os.path.join(settings.upload_dir, subdir, name)
    return StoredFile(path=path, content_type=detected, original_name=_clean_name(original_name), key=key)


def check_image(data: bytes) -> str | None:
    """Quick check that the bytes are a JPEG/PNG/WebP image Pillow can read
    (without decoding every pixel). Returns a problem description or None."""
    if _sniff(data) not in IMAGE_TYPES:
        return "not a JPEG, PNG or WebP image"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        return "invalid or corrupted image"
    return None


def delete_after_commit(db: Db, path: str | None) -> None:
    """Remove a replaced/deleted file only once the database change is final."""
    if path:
        db.on_commit(lambda: delete_file(path))


def delete_file(path: str | None) -> None:
    key = relative_upload_path(path) if path else None
    if key:
        try:
            get_store().delete(key)
        except Exception:  # a missing or unreachable file must never break a request
            log.warning("Could not delete stored file %s", key, exc_info=True)


# ----------------------------------------------------------------- serving

def _media_type(key: str) -> tuple[str | None, dict[str, str]]:
    known_type = _MEDIA_TYPES.get(os.path.splitext(key)[1].lower())
    headers = {"Cache-Control": "private, max-age=3600"}
    if known_type in IMAGE_TYPES:
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return known_type, headers


def file_response(path: str | None, *, media_type: str | None = None, filename: str | None = None,
                  inline: bool = True) -> Response:
    """Serve a stored file to an authenticated caller."""
    key = relative_upload_path(path) if path else None
    if not key:
        raise NotFound("File not found")
    known_type, headers = _media_type(key)
    if known_type is None:
        # never let a browser render something we did not validate
        media_type, inline = "application/octet-stream", False
    return get_store().download_response(key, media_type=known_type or media_type,
                                         filename=filename, inline=inline, headers=headers)


def signed_link_response(key: str) -> Response:
    """Serve the target of a verified `uploads/...?exp=&sig=` link."""
    known_type, headers = _media_type(key)
    return get_store().link_response(key, media_type=known_type or "application/octet-stream", headers=headers)


# ---------------------------------------------------------------- internal

def _read_limited(file: UploadFile, max_mb: int) -> bytes:
    limit = max_mb * 1024 * 1024
    buffer = io.BytesIO()
    while chunk := file.file.read(_CHUNK):
        buffer.write(chunk)
        if buffer.tell() > limit:
            raise PayloadTooLarge(f"Max {max_mb} MB")
    return buffer.getvalue()


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    return None


def _reencode_image(data: bytes, content_type: str) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                img = ImageOps.exif_transpose(img)  # keep phone photos upright
                limit = settings.image_max_dimension
                if max(img.size) > limit:
                    img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                return _encode(img, content_type)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        log.info("Rejected image upload: %s", exc)
        raise UnsupportedMediaType("Invalid or corrupted image file") from exc


def _encode(img: Image.Image, content_type: str) -> bytes:
    out = io.BytesIO()
    fmt = _PIL_FORMATS[content_type]
    if fmt == "JPEG":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, "JPEG", quality=88, optimize=True, progressive=True)
    elif fmt == "PNG":
        if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            img = img.convert("RGBA")
        img.save(out, "PNG", optimize=True)
    else:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.save(out, "WEBP", quality=88)
    return out.getvalue()  # EXIF (camera, GPS location) and XMP metadata are not carried over


def _clean_name(name: str | None) -> str | None:
    if not name:
        return name
    base = os.path.basename(name.replace("\\", "/"))
    return "".join(ch for ch in base if ch.isprintable())[:255] or None
