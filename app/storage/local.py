"""Files on the server's own disk (UPLOAD_DIR). Fine for a single server."""
from __future__ import annotations

import os

from fastapi.responses import FileResponse

from app.core.errors import NotFound
from app.storage.base import is_safe_key


class LocalStore:
    name = "local"

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def path_for(self, key: str) -> str:
        path = os.path.abspath(os.path.join(self.root, key))
        if not is_safe_key(key) or not path.startswith(self.root + os.sep):
            raise NotFound("File not found")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)  # readers never see a half-written file

    def delete(self, key: str) -> None:
        try:
            os.remove(self.path_for(key))
        except (OSError, NotFound):
            pass

    def exists(self, key: str) -> bool:
        try:
            return os.path.isfile(self.path_for(key))
        except NotFound:
            return False

    def iter_keys(self):
        for folder, _, files in os.walk(self.root):
            for name in files:
                if not name.endswith(".part"):
                    yield os.path.relpath(os.path.join(folder, name), self.root).replace(os.sep, "/")

    def download_response(self, key, *, media_type, filename, inline, headers):
        path = self.path_for(key)
        if not os.path.isfile(path):
            raise NotFound("File not found")
        return FileResponse(path, media_type=media_type, filename=filename,
                            content_disposition_type="inline" if inline else "attachment", headers=headers)

    def link_response(self, key, *, media_type, headers):
        return self.download_response(key, media_type=media_type, filename=None, inline=True, headers=headers)
