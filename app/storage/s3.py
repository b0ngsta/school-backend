"""Files in an S3-compatible bucket: AWS S3, Cloudflare R2, OCI Object
Storage (S3 compatibility API), MinIO, …

* Signed `uploads/...` links (photos, payment screenshots) answer with a
  302 redirect to a short-lived pre-signed bucket URL, so image traffic goes
  straight to the bucket instead of through the API servers.
* Authenticated downloads (lesson files, question papers) are streamed
  through the API, so clients keep using their Authorization header.

The bucket stays private; nothing in it is publicly readable.
"""
from __future__ import annotations

from fastapi.responses import RedirectResponse, StreamingResponse

from app.core.errors import NotFound
from app.storage.base import content_disposition, is_safe_key

_CHUNK = 256 * 1024


class S3Store:
    name = "s3"

    def __init__(self, client, bucket: str, prefix: str = "", presign_seconds: int = 900):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.presign_seconds = presign_seconds

    @classmethod
    def from_settings(cls, s) -> S3Store:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url or None,
            region_name=s.s3_region or None,
            aws_access_key_id=s.s3_access_key_id or None,
            aws_secret_access_key=s.s3_secret_access_key or None,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": s.s3_addressing_style},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
                # boto3 >= 1.36 adds CRC checksums to every upload by default; some
                # S3-compatible stores (OCI, older R2/MinIO) reject those requests.
                # Only send/verify them where the S3 API requires it.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        return cls(client, s.s3_bucket, s.s3_prefix, s.s3_presign_seconds)

    def _object_key(self, key: str) -> str:
        if not is_safe_key(key):
            raise NotFound("File not found")
        return self.prefix + key

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._object_key(key), Body=data, ContentType=content_type)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def iter_keys(self):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"][len(self.prefix):]

    def download_response(self, key, *, media_type, filename, inline, headers):
        from botocore.exceptions import ClientError

        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._object_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                raise NotFound("File not found") from exc
            raise
        body = obj["Body"]
        return StreamingResponse(
            body.iter_chunks(_CHUNK),
            media_type=media_type,
            headers={
                **headers,
                "Content-Length": str(obj["ContentLength"]),
                "Content-Disposition": content_disposition(filename, inline),
            },
        )

    def link_response(self, key, *, media_type, headers):
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._object_key(key), "ResponseContentType": media_type},
            ExpiresIn=self.presign_seconds,
        )
        cache = f"private, max-age={max(0, min(300, self.presign_seconds - 60))}"
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": cache})
