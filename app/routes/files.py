"""Serves uploaded files through signed, expiring links (see core/file_urls.py).

There is deliberately no public static mount of the upload folder. With
STORAGE_BACKEND=s3 the link answers with a redirect to the bucket."""
from fastapi import APIRouter

from app.core.errors import Forbidden
from app.core.file_urls import URL_PREFIX, verify_signed_request
from app.services import storage

router = APIRouter(tags=["files"], include_in_schema=False)


@router.get(f"/{URL_PREFIX}/{{file_path:path}}")
def serve_upload(file_path: str, exp: int = 0, sig: str = ""):
    if not verify_signed_request(file_path, exp, sig):
        raise Forbidden("Invalid or expired file link")
    return storage.signed_link_response(file_path)
