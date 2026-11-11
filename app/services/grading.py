"""The school's grading system (migration 0021): percentage range → grade,
the same for every class. Until a school sets its own, the CBSE-style scale
of the performance report applies (so nothing changes for those schools).

    GET /grading → {custom, bands: [{grade, min_percent, max_percent}]}  (highest first)
    PUT /grading {bands: [...]}   managers; an empty list = back to the default

A percentage gets the grade of the highest band whose `min_percent` it
reaches, so "0–20 E, 20–40 D" works as people write it: exactly 20 is a D.
"""
from __future__ import annotations

from collections.abc import Callable

from app.core.errors import BadRequest
from app.db import Db
from app.models.auth import CurrentUser
from app.models.exams import GradingUpdate
from app.services import audit
from app.services.performance import GRADES

MAX_BANDS = 15
Grader = Callable[[float | None], str | None]


def _default_bands() -> list[dict]:
    out, top = [], 100.0
    for floor, name in GRADES:  # highest first
        out.append({"grade": name, "min_percent": float(floor), "max_percent": top})
        top = float(floor)
    return out


def _custom(db: Db) -> list[dict]:
    rows = db.all("SELECT grade, min_percent, max_percent FROM grade_bands WHERE org_id = %s "
                  "ORDER BY min_percent DESC, id", (db.org,))
    return [{"grade": r["grade"], "min_percent": float(r["min_percent"]), "max_percent": float(r["max_percent"])}
            for r in rows]


def get(db: Db) -> dict:
    custom = _custom(db)
    return {"custom": bool(custom), "bands": custom or _default_bands()}


def _grader(bands: list[dict]) -> Grader:
    floors = sorted(((b["min_percent"], b["grade"]) for b in bands), reverse=True)

    def grade(pct: float | None) -> str | None:
        if pct is None:
            return None
        for floor, name in floors:
            if pct >= floor - 0.0001:
                return name
        return None

    return grade


def grader(db: Db) -> Grader:
    """pct → grade with the school's own bands, else the default scale."""
    return _grader(get(db)["bands"])


def custom_grader(db: Db) -> Grader | None:
    """The school's own scale, or None when it has not set one."""
    custom = _custom(db)
    return _grader(custom) if custom else None


def floors(db: Db) -> list[dict]:
    """[{min, grade}] highest first — the shape the performance report sends."""
    return [{"min": b["min_percent"], "grade": b["grade"]} for b in get(db)["bands"]]


def update(db: Db, actor: CurrentUser, body: GradingUpdate) -> dict:
    bands = sorted(body.bands, key=lambda b: b.min_percent)
    if len(bands) > MAX_BANDS:
        raise BadRequest(f"At most {MAX_BANDS} grades")
    seen: set[str] = set()
    previous_top: float | None = None
    for b in bands:
        name = b.grade.strip()
        if not name:
            raise BadRequest("Every row needs a grade (e.g. A, B+, E)")
        if name.lower() in seen:
            raise BadRequest(f"Grade {name} appears twice")
        seen.add(name.lower())
        if b.max_percent <= b.min_percent:
            raise BadRequest(f"Grade {name}: 'to' must be more than 'from'")
        if previous_top is not None and b.min_percent < previous_top - 0.0001:
            raise BadRequest(f"Grade {name}: its range overlaps the grade below it")
        previous_top = b.max_percent
    if bands and bands[0].min_percent > 0.0001:
        raise BadRequest("The lowest grade must start from 0, so every mark gets a grade")
    with db.transaction():
        db.execute("DELETE FROM grade_bands WHERE org_id = %s", (db.org,))
        db.execute_many(
            "INSERT INTO grade_bands (org_id, grade, min_percent, max_percent, position) VALUES (%s, %s, %s, %s, %s)",
            [(db.org, b.grade.strip()[:10], b.min_percent, b.max_percent, i) for i, b in enumerate(bands)])
        audit.record(db, actor, "grading.update", "organisation", db.org,
                     {"bands": [{"grade": b.grade.strip(), "from": b.min_percent, "to": b.max_percent}
                                for b in bands]})
    return get(db)
