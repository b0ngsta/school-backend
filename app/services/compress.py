"""Make uploads small before they reach the bucket (Nov 2026: 50 GB bucket).

Used by `services/storage.py` on every upload and by `python -m app.cli
optimize-uploads` for files that are already stored. Everything here is
best-effort and never makes a file bigger: if a step fails or does not help,
the caller keeps the bytes it had.

- Pictures: longest side capped per folder (profile photos / logos small,
  everything else `IMAGE_MAX_DIMENSION`), re-encoded at `IMAGE_QUALITY`; a big
  PNG without transparency (phone screenshot, scanned page) becomes a JPEG
  when that is at least 30 % smaller.
- PDFs (`PDF_COMPRESS`): pictures inside them (scans, phone photos) larger
  than `PDF_IMAGE_MAX_DIMENSION` are scaled down and stored as JPEG, then the
  file is rewritten with compressed object streams (lossless for text/vector).
  Encrypted, broken or tiny PDFs are left as they are.
"""
from __future__ import annotations

import io
import logging

from PIL import Image

from app.core.config import settings

log = logging.getLogger(__name__)

# folders whose pictures are only ever shown small (avatars, header logo)
SMALL_FOLDERS = frozenset({"photos", "logos"})
# folders where a PNG may become a JPEG (logos keep PNG: flat colours, often reused in print)
_NO_CONVERT_FOLDERS = frozenset({"logos"})

PNG_TO_JPEG_MIN_BYTES = 200 * 1024      # small PNGs (icons, test files) stay PNG
PNG_TO_JPEG_MIN_SAVING = 0.30
PDF_MIN_BYTES = 100 * 1024              # below this a PDF is not worth touching
PDF_MIN_SAVING = 0.05                   # keep the original unless we save at least 5 %


def max_dimension_for(subdir: str | None) -> int:
    limit = settings.image_max_dimension
    if subdir in SMALL_FOLDERS:
        limit = min(limit, settings.photo_max_dimension)
    return limit


def may_convert_png(subdir: str | None) -> bool:
    return subdir not in _NO_CONVERT_FOLDERS


def has_transparency(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA", "PA"):
        lo, _ = img.getchannel("A").getextrema()
        return lo < 255
    return img.mode == "P" and "transparency" in img.info


def png_as_jpeg(img: Image.Image, png: bytes) -> bytes | None:
    """JPEG bytes for an opaque picture when that is clearly smaller than `png`."""
    if len(png) < PNG_TO_JPEG_MIN_BYTES or has_transparency(img):
        return None
    rgb = img.convert("L" if img.mode in ("L", "LA", "1", "I", "I;16") else "RGB")
    out = io.BytesIO()
    rgb.save(out, "JPEG", quality=settings.image_quality, optimize=True, progressive=True)
    jpeg = out.getvalue()
    return jpeg if len(jpeg) <= len(png) * (1 - PNG_TO_JPEG_MIN_SAVING) else None


# --------------------------------------------------------------------- PDFs

def shrink_pdf(data: bytes) -> bytes:
    """A smaller copy of the PDF, or `data` itself when that is not possible."""
    if not settings.pdf_compress or len(data) < PDF_MIN_BYTES:
        return data
    try:
        import pikepdf
    except ImportError:  # optional at runtime; uploads still work, just uncompressed
        log.warning("pikepdf is not installed - PDFs are stored without compression")
        return data
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            if pdf.is_encrypted:
                return data
            images = _downsample_images(pdf, settings.pdf_image_max_dimension, settings.image_quality)
            out = io.BytesIO()
            pdf.save(out, compress_streams=True, recompress_flate=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate)
        result = out.getvalue()
        with pikepdf.open(io.BytesIO(result)) as check:  # must still open, with every page
            if len(check.pages) == 0:
                return data
    except Exception as exc:  # noqa: BLE001 - a PDF we cannot rewrite is stored as uploaded
        log.info("PDF left uncompressed: %s", exc)
        return data
    if len(result) > len(data) * (1 - PDF_MIN_SAVING):
        return data
    log.info("PDF %d -> %d bytes (%d pictures scaled)", len(data), len(result), images)
    return result


def _downsample_images(pdf, max_dim: int, quality: int) -> int:
    from pikepdf import Name

    done = 0
    seen: set[tuple[int, int]] = set()
    for page in pdf.pages:
        try:
            finder = getattr(page, "get_images", None)  # pikepdf >= 10 (also finds pictures in forms)
            images = list((finder() if finder else page.images).values())
        except Exception:  # noqa: BLE001
            continue
        for raw in images:
            if raw.objgen in seen:
                continue
            seen.add(raw.objgen)
            try:
                if _shrink_image(raw, max_dim, quality, Name):
                    done += 1
            except Exception:  # noqa: BLE001 - leave that picture as it was
                continue
    return done


def _shrink_image(raw, max_dim: int, quality: int, Name) -> bool:
    from pikepdf import PdfImage

    width, height = int(raw.get("/Width", 0)), int(raw.get("/Height", 0))
    if max(width, height) <= max_dim or width * height > Image.MAX_IMAGE_PIXELS:
        return False
    # only plain 8-bit RGB / grey pictures; masks, decode arrays, CMYK, indexed, 1-bit stay untouched
    if raw.get("/ImageMask") or "/Mask" in raw or "/Decode" in raw or int(raw.get("/BitsPerComponent", 8)) != 8:
        return False
    img = PdfImage(raw).as_pil_image()
    if img.mode not in ("RGB", "L"):
        return False
    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    jpeg = out.getvalue()
    if len(jpeg) >= len(raw.read_raw_bytes()):
        return False
    if "/DecodeParms" in raw:
        del raw["/DecodeParms"]
    raw.write(jpeg, filter=Name.DCTDecode)
    raw.Width, raw.Height = img.size
    raw.ColorSpace = Name.DeviceRGB if img.mode == "RGB" else Name.DeviceGray
    raw.BitsPerComponent = 8
    return True
