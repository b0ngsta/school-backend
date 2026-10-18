"""Where uploaded files live: the server's disk or an S3-compatible bucket
(STORAGE_BACKEND=local|s3). The rest of the app only talks to `get_store()`."""
from __future__ import annotations

import threading

from app.core.config import settings
from app.storage.base import BlobStore

_store: BlobStore | None = None
_lock = threading.Lock()


def get_store() -> BlobStore:
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = _build()
    return _store


def set_store(store: BlobStore | None) -> None:
    """Swap the store (tests, maintenance scripts)."""
    global _store
    _store = store


def _build() -> BlobStore:
    if settings.storage_backend == "s3":
        from app.storage.s3 import S3Store

        return S3Store.from_settings(settings)
    from app.storage.local import LocalStore

    return LocalStore(settings.upload_dir)
