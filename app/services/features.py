"""Per-school feature flags: resolution, cache and the HQ (platform) actions.

Table `organisation_features (org_id, feature_key, enabled, note, updated_by, …)`
holds ONLY overrides; no row = the catalogue default (on). Unknown keys in the
table are ignored, so removing a feature from the catalogue is safe.

No extra query per request: `auth.authenticate()` already reads the user's
`organisations` row and now also takes `features_version`. Each worker keeps the
resolved flags per school keyed by that version; any change from HQ bumps the
version in the same transaction, so the very next request (on every worker /
server) re-reads the school's rows once.
"""
from __future__ import annotations

from typing import Any

from app.core.errors import BadRequest, NotFound
from app.core.features import CATALOGUE, DEFAULTS, FEATURES
from app.db import Db
from app.services import audit, organisations

_cache: dict[int, tuple[int, dict[str, bool]]] = {}


def resolve(rows: list[dict]) -> dict[str, bool]:
    flags = dict(DEFAULTS)
    for r in rows:
        if r["feature_key"] in FEATURES:
            flags[r["feature_key"]] = bool(r["enabled"])
    return flags


def _read(db: Db, org_id: int) -> dict[str, bool]:
    return resolve(db.all("SELECT feature_key, enabled FROM organisation_features WHERE org_id = %s", (org_id,)))


def for_org(db: Db, org_id: int, version: int | None = None) -> dict[str, bool]:
    """The school's effective flags {key: bool}. `version` = organisations.features_version
    when the caller already read it (authenticate); else it is read here."""
    if version is None:
        row = db.one("SELECT features_version FROM organisations WHERE id = %s", (org_id,))
        version = int(row["features_version"]) if row else 0
    hit = _cache.get(org_id)
    if hit and hit[0] == version:
        return hit[1]
    flags = _read(db, org_id)
    _cache[org_id] = (version, flags)  # a plain dict assignment is atomic under the GIL
    return flags


def off_by_org(db: Db) -> dict[int, set[str]]:
    """Every school's switched-off features (one query; for the platform rows / jobs)."""
    off: dict[int, set[str]] = {}
    for r in db.all("SELECT org_id, feature_key FROM organisation_features WHERE enabled = 0"):
        if r["feature_key"] in FEATURES:
            off.setdefault(r["org_id"], set()).add(r["feature_key"])
    return off


def orgs_with_off(db: Db, key: str) -> set[int]:
    return {oid for oid, keys in off_by_org(db).items() if key in keys}


# ------------------------------------------------------------- platform (HQ)

def _iso(value: Any) -> str | None:
    from app.services.platform import _school_iso  # local import: platform imports this module
    return _school_iso(value)


def school_features(db: Db, org_id: int) -> list[dict]:
    """Every catalogue feature for one school: effective value + where it comes from."""
    rows = {r["feature_key"]: r for r in db.all(
        "SELECT feature_key, enabled, note, updated_by, updated_at FROM organisation_features WHERE org_id = %s",
        (org_id,))}
    out = []
    for f in CATALOGUE:
        r = rows.get(f.key)
        out.append({
            "key": f.key,
            "label": f.label,
            "description": f.description,
            "default": f.default,
            "enabled": bool(r["enabled"]) if r else f.default,
            "source": "override" if r else "default",
            "note": r["note"] if r else None,
            "updated_by": r["updated_by"] if r else None,
            "updated_at": _iso(r["updated_at"]) if r else None,
        })
    return out


def get_for_school(db: Db, code: str) -> list[dict]:
    org = organisations.find(db, code)
    if org is None:
        raise NotFound("School not found")
    return school_features(db, org["id"])


def update_for_school(db: Db, code: str, changes: dict[str, bool | None], note: str | None,
                      operator: str | None) -> list[dict]:
    """Apply overrides: True / False = switch on / off, None = back to the default.
    One transaction: rows, features_version + 1, audit `organisation.features_update`."""
    unknown = sorted(k for k in changes if k not in FEATURES)
    if unknown:
        raise BadRequest(f"Unknown feature: {', '.join(unknown)}")
    note = (note or "").strip()[:255] or None
    with db.transaction():
        org = db.one("SELECT id FROM organisations WHERE code = %s FOR UPDATE",
                     (organisations.normalize_code(code),))
        if org is None:
            raise NotFound("School not found")
        oid = org["id"]
        before = _read(db, oid)
        for key, value in changes.items():
            if value is None:
                db.execute("DELETE FROM organisation_features WHERE org_id = %s AND feature_key = %s", (oid, key))
            else:
                db.execute(
                    "INSERT INTO organisation_features (org_id, feature_key, enabled, note, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), note = VALUES(note), "
                    "updated_by = VALUES(updated_by)",
                    (oid, key, int(bool(value)), note, operator))
        db.execute("UPDATE organisations SET features_version = features_version + 1 WHERE id = %s", (oid,))
        _cache.pop(oid, None)
        after = _read(db, oid)
        diff = {k: [before[k], after[k]] for k in changes if before.get(k) != after.get(k)}
        db.bind_org(oid)
        audit.record(db, None, "organisation.features_update", "organisation", oid, {
            "changes": diff, "requested": changes,
            **({"note": note} if note else {}), **({"operator": operator} if operator else {}),
        })
        return school_features(db, oid)
