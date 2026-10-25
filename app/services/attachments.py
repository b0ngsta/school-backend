"""One file (a PDF or a photo) attached to a record: homework, notices,
messages, timetables, syllabus. Stored like every other upload (validated by
its bytes, images re-encoded); the record keeps three columns:

    attachment_path   "uploads/<folder>/<random>.pdf"  (signed on the way out)
    attachment_name   the uploader's file name, for display
    attachment_type   "pdf" | "image"  — clients pick the viewer from it
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import UploadFile

from app.db import Db
from app.services import storage

TYPES = storage.IMAGE_TYPES | storage.PDF_TYPES
MAX_MB = 15
TYPE_ERROR = "Attach a PDF or a photo (JPEG, PNG or WebP)"


@dataclass(frozen=True)
class Attachment:
    path: str
    name: str
    type: str  # 'pdf' | 'image'

    def columns(self) -> tuple[str, str, str]:
        return self.path, self.name, self.type


def save(db: Db, file: UploadFile, subdir: str, default_name: str = "attachment") -> Attachment:
    """Validate and store the file. Must run inside `db.transaction()`."""
    stored = storage.save_upload(db, file, subdir, TYPES, max_mb=MAX_MB, type_error=TYPE_ERROR)
    kind = "pdf" if stored.content_type in storage.PDF_TYPES else "image"
    name = stored.original_name or f"{default_name}{storage.EXTENSIONS[stored.content_type]}"
    return Attachment(stored.path, name[:255], kind)
