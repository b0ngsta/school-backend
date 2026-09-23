"""Shared file-upload helpers."""
import os
import secrets

from fastapi import HTTPException, UploadFile, status

from app.config import UPLOAD_DIR

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
PDF_TYPES = {"application/pdf"}


def save_upload(
    file: UploadFile,
    subdir: str,
    allowed_types: set[str],
    max_mb: int = 10,
) -> str:
    if file.content_type not in allowed_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"File type not allowed: {file.content_type}",
        )
    data = file.file.read()
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Max {max_mb} MB")
    ext = os.path.splitext(file.filename or "")[1] or ".bin"
    folder = os.path.join(UPLOAD_DIR, subdir)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{secrets.token_hex(8)}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def delete_file(path: str | None):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
