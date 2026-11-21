"""Easy first passwords: a short everyday word and four digits, e.g. "mango4821".

Used where the system makes a password for someone else (bulk school import,
"New password & share" for a student's login). Lower-case letters and digits
only, so it is easy to read out and type on a phone: no symbols, no capitals,
no look-alike letters to mistake. Every account given such a password must
choose its own at the next sign-in (`must_change_password = 1`), and sign-in
throttling + CAPTCHA (services/login_throttle.py) stop guessing.
"""
from __future__ import annotations

import secrets

from app.core.security import hash_password

# short, common, unambiguous words (no l/1 or o/0 confusion matters here: digits come last)
WORDS = (
    "apple", "mango", "grape", "lemon", "peach", "melon", "berry", "guava", "papaya", "banana",
    "tiger", "zebra", "panda", "eagle", "camel", "horse", "rabbit", "parrot", "monkey", "turtle",
    "river", "ocean", "cloud", "storm", "sunny", "rainy", "earth", "planet", "comet", "rocket",
    "piano", "drum", "flute", "guitar", "violin", "music", "dance", "story", "poem", "chess",
    "green", "blue", "pink", "purple", "silver", "golden", "orange", "yellow", "brown", "white",
    "table", "chair", "pencil", "eraser", "paper", "school", "garden", "window", "bridge", "castle",
    "candle", "basket", "button", "carpet", "pillow", "mirror", "ladder", "bottle", "rubber", "ribbon",
    "tomato", "potato", "carrot", "onion", "ginger", "pepper", "butter", "cookie", "muffin", "noodle",
    "cricket", "hockey", "tennis", "kabaddi", "football", "runner", "winner", "player", "kite", "ball",
    "lotus", "rose", "tulip", "daisy", "jasmine", "maple", "cedar", "bamboo", "forest", "meadow",
)


def easy() -> str:
    """A new random easy password (about 2^20 possibilities: fine for a first
    password that must be changed at sign-in, behind login throttling)."""
    return f"{secrets.choice(WORDS)}{secrets.randbelow(9000) + 1000}"


def unusable_hash() -> str:
    """A password hash nobody knows the password of (a random 32-byte secret,
    thrown away). Accounts created without a password (students from the bulk
    school import) store it: nobody can sign in until the school gives a
    password ("New password & share" or the office's Set password). It is a real
    Argon2 hash, so a sign-in attempt costs the same time as for any account."""
    return hash_password(secrets.token_urlsafe(32))
