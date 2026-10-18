"""The optional photos ZIP uploaded with a bulk import.

The CSV's `photo` column names a file inside the ZIP ("ADM1023.jpg");
folders inside the ZIP are ignored and names match case-insensitively.
Nothing is extracted to disk: each referenced photo is read into memory
(with a size cap that does not trust the ZIP's own headers), checked, and
on save goes through the same validation and re-encoding as a single photo
upload (EXIF/GPS removed, max 2560 px).
"""
from __future__ import annotations

import posixpath
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

from fastapi import UploadFile

from app.core.errors import BadRequest, PayloadTooLarge
from app.services import storage

PHOTO_MAX_MB = 5
MAX_ENTRIES = 5000
_WORKERS = 4


def photo_key(name: str) -> str:
    """How a photo is looked up: file name only, case-insensitive."""
    return posixpath.basename(name.replace("\\", "/")).strip().lower()


def _usable(name: str) -> bool:
    base = posixpath.basename(name)
    return bool(base) and not base.startswith(".") and not name.startswith("__MACOSX/")


class PhotoArchive:
    def __init__(self, upload: UploadFile, max_mb: int):
        fh = upload.file
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(0)
        if size > max_mb * 1024 * 1024:
            raise PayloadTooLarge(f"The photos ZIP is larger than {max_mb} MB. Resize the photos or split the "
                                  "import into smaller groups.")
        try:
            self._zip = zipfile.ZipFile(fh)
            entries = [info for info in self._zip.infolist() if not info.is_dir() and _usable(info.filename)]
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
            raise BadRequest("The photos file is not a valid ZIP archive.") from exc
        if len(entries) > MAX_ENTRIES:
            raise BadRequest(f"The photos ZIP has more than {MAX_ENTRIES} files.")

        self._entries: dict[str, zipfile.ZipInfo] = {}
        self._ambiguous: set[str] = set()
        for info in entries:
            key = photo_key(info.filename)
            if key in self._entries:
                self._ambiguous.add(key)
            self._entries[key] = info
        self._used: set[str] = set()
        self._read_lock = threading.Lock()

    # ----------------------------------------------------------- checking

    def problem(self, name: str) -> str | None:
        """Why the photo named in the CSV cannot be used, or None."""
        key = photo_key(name)
        if key in self._ambiguous:
            return f"Photo '{name}': the ZIP contains several files with this name (in different folders)."
        info = self._entries.get(key)
        if info is None:
            return f"Photo '{name}' is not in the photos ZIP."
        if info.flag_bits & 0x1:
            return f"Photo '{name}' is password-protected inside the ZIP."
        try:
            data = self._read(info)
        except PayloadTooLarge:
            return f"Photo '{name}' is larger than {PHOTO_MAX_MB} MB."
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError):
            return f"Photo '{name}' could not be read from the ZIP."
        issue = storage.check_image(data)
        if issue:
            return f"Photo '{name}': {issue}."
        self._used.add(key)
        return None

    def unused(self) -> list[str]:
        return sorted(posixpath.basename(info.filename) for key, info in self._entries.items()
                      if key not in self._used)

    # ------------------------------------------------------------- saving

    def store_all(self, names: list[str]) -> dict[str, storage.StoredFile]:
        """Re-encode and store the photos (in parallel); returns photo_key ->
        stored file. If any photo fails, the ones already stored are removed
        and the error is raised."""
        keys = sorted({photo_key(n) for n in names})
        with ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="photo") as pool:
            futures = {key: pool.submit(self._store_one, key) for key in keys}
        stored = {key: f.result() for key, f in futures.items() if f.exception() is None}
        failures = [f.exception() for f in futures.values() if f.exception() is not None]
        if failures:
            remove(list(stored.values()))
            raise failures[0]
        return stored

    def _store_one(self, key: str) -> storage.StoredFile:
        info = self._entries[key]
        return storage.store_bytes(self._read(info), "photos", storage.IMAGE_TYPES, original_name=info.filename)

    def _read(self, info: zipfile.ZipInfo) -> bytes:
        if info.file_size > PHOTO_MAX_MB * 1024 * 1024:
            raise PayloadTooLarge(f"Max {PHOTO_MAX_MB} MB")
        limit = PHOTO_MAX_MB * 1024 * 1024
        with self._read_lock, self._zip.open(info) as member:  # sizes in ZIP headers can lie
            data = member.read(limit + 1)
        if len(data) > limit:
            raise PayloadTooLarge(f"Max {PHOTO_MAX_MB} MB")
        return data


def remove(files: list[storage.StoredFile]) -> None:
    for f in files:
        storage.delete_file(f.path)
