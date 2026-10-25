"""Uploads stored in an S3-compatible bucket (runs against a local mock S3)."""
import socket
from io import BytesIO

import boto3
import httpx
import pytest
from botocore.config import Config
from helpers import PDF_BYTES, bearer, jpeg_with_gps, login, png_bytes
from PIL import Image

from app import storage as storage_registry
from app.storage.s3 import S3Store

moto_server = pytest.importorskip("moto.server")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def s3_endpoint():
    port = _free_port()
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def bucket(s3_endpoint):
    client = boto3.client("s3", endpoint_url=s3_endpoint, region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test",
                          config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    name = "school-uploads"
    client.create_bucket(Bucket=name)
    store = S3Store(client, name, prefix="school1", presign_seconds=300)
    storage_registry.set_store(store)
    yield client, name
    storage_registry.set_store(None)
    for obj in client.list_objects_v2(Bucket=name).get("Contents", []):
        client.delete_object(Bucket=name, Key=obj["Key"])
    client.delete_bucket(Bucket=name)


def _keys(client, name):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=name).get("Contents", []))


def test_photo_goes_to_the_bucket_and_link_redirects(school, bucket):
    client, name = bucket
    r = school.client.post(f"/users/{school.t1_id}/photo", headers=bearer(school.admin),
                           files={"file": ("me.jpg", jpeg_with_gps(800, 600), "image/jpeg")})
    assert r.status_code == 200
    link = r.json()["photo_path"]
    assert link.startswith("uploads/photos/")                     # same link format as before
    [key] = _keys(client, name)
    assert key.startswith("school1/photos/") and key.endswith(".jpg")
    assert client.head_object(Bucket=name, Key=key)["ContentType"] == "image/jpeg"

    redirect = school.client.get("/" + link, follow_redirects=False)
    assert redirect.status_code == 302
    image = httpx.get(redirect.headers["location"])                 # pre-signed bucket URL
    assert image.status_code == 200
    assert Image.open(BytesIO(image.content)).size == (800, 600)

    assert school.client.get("/" + link.split("?")[0], follow_redirects=False).status_code == 403


def test_replaced_and_rolled_back_files_are_removed_from_the_bucket(school, bucket):
    client, name = bucket
    a = bearer(school.admin)
    school.client.post(f"/users/{school.t1_id}/photo", headers=a, files={"file": ("a.png", png_bytes(), "image/png")})
    first = _keys(client, name)
    school.client.post(f"/users/{school.t1_id}/photo", headers=a, files={"file": ("b.png", png_bytes(), "image/png")})
    second = _keys(client, name)
    assert len(first) == len(second) == 1 and first != second       # old object deleted after commit

    bad = school.client.post("/lessons", headers=bearer(school.t1),
                             data={"class_id": str(school.c1), "heading": "x"},
                             files=[("files", ("ok.png", png_bytes(), "image/png")),
                                    ("files", ("bad.png", b"not an image", "image/png"))])
    assert bad.status_code == 415
    assert _keys(client, name) == second                            # first file of the failed plan removed


def test_authenticated_downloads_stream_from_the_bucket(school, bucket):
    c = school.client
    r = c.post("/lessons", headers=bearer(school.t1), data={"class_id": str(school.c1), "heading": "Notes"},
               files=[("files", ("Notes – week 1.pdf", PDF_BYTES, "application/pdf"))])
    assert r.status_code == 201
    file_id = r.json()["files"][0]["id"]
    download = c.get(f"/lessons/files/{file_id}", headers=bearer(school.t1))
    assert download.status_code == 200
    assert download.content == PDF_BYTES
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"].startswith("attachment; filename*=utf-8''Notes")
    assert c.get(f"/lessons/files/{file_id}", headers=bearer(login(c, "student1", "studpass"))).status_code == 403


def test_check_storage_against_the_bucket(bucket, capsys):
    from app.cli import check_storage

    client, name = bucket
    check_storage()
    assert "Storage OK: s3 (s3://school-uploads/school1/)" in capsys.readouterr().out
    assert _keys(client, name) == []                                # test object removed again


def test_client_only_sends_checksums_when_required():
    """boto3 >= 1.36 checksums every upload by default; R2/OCI/MinIO can refuse that."""
    from types import SimpleNamespace

    s = SimpleNamespace(s3_endpoint_url="https://example.r2.cloudflarestorage.com", s3_region="auto",
                        s3_access_key_id="k", s3_secret_access_key="s", s3_addressing_style="path",
                        s3_bucket="b", s3_prefix="", s3_presign_seconds=900)
    config = S3Store.from_settings(s).client.meta.config
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"


def test_copying_existing_uploads_to_the_bucket(tmp_path, bucket):
    from app.cli import copy_uploads
    from app.storage.local import LocalStore

    client, name = bucket
    local = LocalStore(str(tmp_path))
    local.put("photos/a.jpg", b"jpeg-bytes", "image/jpeg")
    local.put("papers/p.pdf", PDF_BYTES, "application/pdf")
    target = storage_registry.get_store()
    assert copy_uploads(local, target) == (2, 0)
    assert copy_uploads(local, target) == (0, 2)                    # second run skips what is there
    assert _keys(client, name) == ["school1/papers/p.pdf", "school1/photos/a.jpg"]
    assert client.head_object(Bucket=name, Key="school1/papers/p.pdf")["ContentType"] == "application/pdf"
