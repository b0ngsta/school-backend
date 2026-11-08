"""Uploads are made small for the bucket (services/compress.py) and nothing
that worked before changes: same types allowed, same links, never bigger."""
from io import BytesIO

import pikepdf
from helpers import PDF_BYTES, bearer, jpeg_with_gps, png_bytes
from PIL import Image

from app import storage as storage_registry
from app.cli import optimize_uploads
from app.services import storage
from app.storage.local import LocalStore


def _noisy(w: int, h: int) -> Image.Image:
    return Image.effect_noise((w // 4, h // 4), 60).convert("RGB").resize((w, h))


def _save(img: Image.Image, fmt: str, **kw) -> bytes:
    out = BytesIO()
    img.save(out, fmt, **kw)
    return out.getvalue()


def _scanned_pdf(pages: int = 2) -> bytes:
    pdf = pikepdf.new()
    for _ in range(pages):
        page = pikepdf.Pdf.open(BytesIO(_save(_noisy(2480, 3508), "PDF", resolution=300)))
        pdf.pages.extend(page.pages)
    out = BytesIO()
    pdf.save(out)
    return out.getvalue()


def test_pictures_are_scaled_per_folder(tmp_path):
    storage_registry.set_store(LocalStore(str(tmp_path)))
    try:
        big = _save(_noisy(4000, 3000), "JPEG", quality=95)
        photo = storage.store_bytes(big, "photos", storage.IMAGE_TYPES, "me.jpg")
        notice = storage.store_bytes(big, "notices", storage.IMAGE_TYPES | storage.PDF_TYPES, "n.jpg")
        assert Image.open(tmp_path / photo.key).size == (800, 600)
        assert Image.open(tmp_path / notice.key).size == (1920, 1440)
        assert (tmp_path / photo.key).stat().st_size < len(big) / 10

        shot = _save(_noisy(1080, 2400), "PNG")                       # opaque phone screenshot
        claim = storage.store_bytes(shot, "claims", storage.IMAGE_TYPES, "proof.png")
        assert claim.content_type == "image/jpeg" and claim.key.endswith(".jpg") and claim.original_name == "proof.jpg"

        logo = storage.store_bytes(shot, "logos", storage.IMAGE_TYPES, "logo.png")   # logos keep PNG
        small = storage.store_bytes(png_bytes(), "photos", storage.IMAGE_TYPES, "a.png")  # tiny / transparent
        assert logo.content_type == small.content_type == "image/png"
    finally:
        storage_registry.set_store(None)


def test_pdfs_shrink_and_odd_pdfs_stay_as_they_are(tmp_path):
    storage_registry.set_store(LocalStore(str(tmp_path)))
    try:
        scanned = _scanned_pdf()
        stored = storage.store_bytes(scanned, "documents", storage.PDF_TYPES, "s.pdf")
        data = (tmp_path / stored.key).read_bytes()
        assert len(data) < len(scanned) * 0.8
        with pikepdf.open(BytesIO(data)) as pdf:
            assert len(pdf.pages) == 2
        for odd in (PDF_BYTES, b"%PDF-1.7\n" + bytes(range(256)) * 800):
            kept = storage.store_bytes(odd, "papers", storage.PDF_TYPES, "x.pdf")
            assert (tmp_path / kept.key).read_bytes() == odd
    finally:
        storage_registry.set_store(None)


def test_optimize_uploads_shrinks_in_place_once(tmp_path):
    store = LocalStore(str(tmp_path))
    store.put("photos/old.jpg", _save(_noisy(3000, 2000), "JPEG", quality=95), "image/jpeg")
    store.put("papers/old.pdf", _scanned_pdf(1), "application/pdf")
    store.put("papers/tiny.pdf", PDF_BYTES, "application/pdf")
    assert optimize_uploads(store, dry_run=True)[1] == 2
    assert (tmp_path / "photos/old.jpg").stat().st_size > 500_000            # dry run wrote nothing
    checked, shrunk, before, after = optimize_uploads(store)
    assert (checked, shrunk) == (3, 2) and after < before / 2
    assert Image.open(tmp_path / "photos/old.jpg").size == (800, 533)         # same key, smaller
    assert (tmp_path / "papers/tiny.pdf").read_bytes() == PDF_BYTES
    assert optimize_uploads(store)[1] == 0                                    # second run: nothing left


def test_upload_through_the_api_is_small(school):
    r = school.client.post(f"/users/{school.t1_id}/photo", headers=bearer(school.admin),
                           files={"file": ("me.jpg", jpeg_with_gps(3000, 2000), "image/jpeg")})
    assert r.status_code == 200 and r.json()["photo_path"].split("?")[0].endswith(".jpg")
    image = school.client.get("/" + r.json()["photo_path"])
    assert max(Image.open(BytesIO(image.content)).size) == 800
