"""Picture challenges for sign-in (migration 0014).

A CAPTCHA is asked for instead of refusing a sign-in outright: after a few
failures on an account, or many from one address (services/login_throttle).
It is drawn here with Pillow, so no third-party service sees who signs in
(children's data, DPDP) and the apps need no extra native library: they show
the PNG and send back what the person typed.

* POST /auth/captcha        -> {captcha_id, image: "data:image/png;base64,…", expires_in, length}
* POST /auth/login          -> {…, captcha_id, captcha_answer}

Each picture works once and expires after CAPTCHA_TTL_SECONDS. Only a keyed
hash of the answer is stored. Letters that look alike (0/O, 1/I/L) are never used,
and answers are not case-sensitive.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
from datetime import timedelta

from PIL import Image, ImageDraw, ImageFont

from app.core.clock import utcnow
from app.core.config import settings
from app.core.context import client_ip
from app.core.errors import AppError, TooManyRequests
from app.db import Db

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LENGTH = 5
_KEY = hmac.new(settings.secret_key.encode(), b"school-api:captcha", hashlib.sha256).digest()
_WIDTH, _HEIGHT = 180, 64


class CaptchaRequired(AppError):
    """428: the sign-in needs a solved picture first (`code` tells the app to show one)."""

    status_code = 428

    def __init__(self, invalid: bool = False):
        if invalid:
            super().__init__("Those characters didn't match. Type the characters in the new picture.",
                             extra={"code": "captcha_invalid"})
        else:
            super().__init__("Type the characters in the picture to continue.",
                             extra={"code": "captcha_required"})


def _digest(captcha_id: str, answer: str) -> str:
    normalised = "".join(answer.split()).upper()
    return hmac.new(_KEY, f"{captcha_id}:{normalised}".encode(), hashlib.sha256).hexdigest()


def new_text() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)  # FreeType build of Pillow (all standard wheels)
    except (TypeError, OSError):  # pragma: no cover - very old / minimal Pillow
        return ImageFont.load_default()


def render(text: str) -> bytes:
    """A small PNG: each letter tilted and shifted, with lines and dots behind."""
    rnd = secrets.SystemRandom()
    img = Image.new("RGB", (_WIDTH, _HEIGHT), (246, 247, 250))
    draw = ImageDraw.Draw(img)
    for _ in range(6):
        shade = rnd.randint(150, 205)
        draw.line([(rnd.randint(0, _WIDTH), rnd.randint(0, _HEIGHT)),
                   (rnd.randint(0, _WIDTH), rnd.randint(0, _HEIGHT))], fill=(shade, shade, shade), width=2)
    font = _font(38)
    x = 10
    for ch in text:
        glyph = Image.new("RGBA", (46, 60), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((7, 4), ch, font=font,
                                   fill=(rnd.randint(10, 80), rnd.randint(20, 90), rnd.randint(70, 150), 255))
        glyph = glyph.rotate(rnd.uniform(-24, 24), resample=Image.Resampling.BICUBIC)
        img.paste(glyph, (x, rnd.randint(0, 6)), glyph)
        x += rnd.randint(29, 33)
    for _ in range(140):
        shade = rnd.randint(110, 200)
        draw.point((rnd.randint(0, _WIDTH - 1), rnd.randint(0, _HEIGHT - 1)), fill=(shade, shade, shade))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def issue(db: Db) -> dict:
    now, ip = utcnow(), client_ip()
    recent = db.scalar(
        "SELECT COUNT(*) FROM auth_captchas WHERE ip <=> %s AND created_at >= %s",
        (ip, now - timedelta(minutes=settings.login_window_minutes)),
    )
    if int(recent or 0) >= settings.captcha_max_per_ip:
        raise TooManyRequests("Too many pictures requested. Try again in a few minutes.",
                              retry_after_seconds=settings.login_window_minutes * 60)
    captcha_id, text = secrets.token_hex(16), new_text()
    with db.transaction():
        db.execute(
            "INSERT INTO auth_captchas (id, answer_digest, ip, created_at, expires_at) VALUES (%s, %s, %s, %s, %s)",
            (captcha_id, _digest(captcha_id, text), ip, now, now + timedelta(seconds=settings.captcha_ttl_seconds)),
        )
        db.execute("DELETE FROM auth_captchas WHERE expires_at < %s LIMIT 200", (now - timedelta(hours=1),))
    image = base64.b64encode(render(text)).decode()
    return {"captcha_id": captcha_id, "image": f"data:image/png;base64,{image}",
            "expires_in": settings.captcha_ttl_seconds, "length": LENGTH}


def verify(db: Db, captcha_id: str | None, answer: str | None) -> None:
    """Use up the picture; raise CaptchaRequired unless the answer is right."""
    if not captcha_id or not answer:
        raise CaptchaRequired()
    ok = False
    with db.transaction():
        row = db.one("SELECT answer_digest, expires_at, used_at FROM auth_captchas WHERE id = %s FOR UPDATE",
                     (captcha_id[:32],))
        if row is not None and row["used_at"] is None:
            db.execute("UPDATE auth_captchas SET used_at = %s WHERE id = %s", (utcnow(), captcha_id[:32]))
            ok = row["expires_at"] > utcnow() and hmac.compare_digest(row["answer_digest"],
                                                                      _digest(captcha_id[:32], answer))
    if not ok:  # the "used" mark above is committed: a picture can't be guessed twice
        raise CaptchaRequired(invalid=True)
