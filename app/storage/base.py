"""The interface every file store implements, plus shared HTTP helpers."""
from __future__ import annotations

from typing import Protocol
from urllib.parse import quote

from starlette.responses import Response


class BlobStore(Protocol):
    """Stores uploaded files under keys like "photos/<hex>.jpg"."""

    name: str

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def iter_keys(self): ...

    def download_response(self, key: str, *, media_type: str, filename: str | None,
                          inline: bool, headers: dict[str, str]) -> Response:
        """Response for an authenticated download endpoint (served through the API)."""
        ...

    def link_response(self, key: str, *, media_type: str, headers: dict[str, str]) -> Response:
        """Response for a signed `uploads/...` link (may redirect to the store)."""
        ...


def content_disposition(filename: str | None, inline: bool) -> str:
    """Same header Starlette's FileResponse builds (RFC 6266 / 5987)."""
    kind = "inline" if inline else "attachment"
    if not filename:
        return kind
    quoted = quote(filename)
    if quoted != filename:
        return f"{kind}; filename*=utf-8''{quoted}"
    return f'{kind}; filename="{filename}"'


def is_safe_key(key: str) -> bool:
    parts = key.split("/")
    return bool(key) and not key.startswith("/") and all(p not in ("", ".", "..") for p in parts)
