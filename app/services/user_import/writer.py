"""Saving a validated import plan in a few multi-row statements.

Round trips matter more than rows here (the database may be in another
region), so new users are inserted with multi-row INSERTs and changed
fields are applied with one CASE-based UPDATE per 200 users, instead of one
statement per user.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.security import hash_password
from app.db import Db
from app.services import storage
from app.services.user_import.columns import STUDENT_FIELDS, TEACHER_FIELDS
from app.services.user_import.photos import photo_key
from app.services.user_import.planner import CREATE, UPDATE, Plan, RowPlan, fold

_UPDATE_CHUNK = 200
_USER_FIELDS = ("full_name", "email", "phone")
_STUDENT_FIELDS = tuple(c.name for c in STUDENT_FIELDS)
_TEACHER_FIELDS = tuple(c.name for c in TEACHER_FIELDS)


def hash_passwords(plan: Plan) -> dict[int, str]:
    """Argon2 is deliberately slow (~50 ms); hash new users' passwords in
    parallel, before the transaction starts. Returns row number -> hash."""
    rows = plan.rows_with(CREATE)
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2), thread_name_prefix="hash") as pool:
        hashes = list(pool.map(hash_password, [r.values["password"] for r in rows]))
    return {r.number: h for r, h in zip(rows, hashes, strict=True)}


def apply(db: Db, plan: Plan, hashes: dict[int, str], photos: dict[str, storage.StoredFile]) -> None:
    """Write the plan. Call inside a transaction, with `plan` built under
    lock. Sets `user_id` on created rows."""
    creates, updates = plan.rows_with(CREATE), plan.rows_with(UPDATE)
    profile_fields = _profile_fields(plan.user_type)
    if plan.user_type == "student":
        _free_moving_numbers(db, updates)
    if creates:
        _insert_users(db, plan, creates, hashes, photos)
        if profile_fields:
            _insert_profiles(db, plan, creates, profile_fields)
    if updates:
        _update_users(db, updates, photos)
        if profile_fields:
            _upsert_profiles(db, plan, [r for r in updates if set(r.changes) & set(profile_fields)], profile_fields)


def _profile_fields(user_type: str) -> tuple[str, ...]:
    return _STUDENT_FIELDS if user_type == "student" else _TEACHER_FIELDS if user_type == "teacher" else ()


def _photo_path(row: RowPlan, photos: dict[str, storage.StoredFile]) -> str | None:
    name = row.values.get("photo")
    return photos[photo_key(name)].path if name else None


# ------------------------------------------------------------- creating

def _insert_users(db: Db, plan: Plan, rows: list[RowPlan], hashes: dict[int, str],
                  photos: dict[str, storage.StoredFile]) -> None:
    db.execute_many(
        "INSERT INTO users (org_id, username, password_hash, full_name, email, phone, user_type, photo_path, "
        "must_change_password) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)",
        [(db.org, r.username, hashes[r.number], r.values["full_name"], r.values["email"], r.values.get("phone"),
          plan.user_type, _photo_path(r, photos)) for r in rows],
    )
    ids = _ids_by_username(db, [r.username for r in rows])
    for r in rows:
        r.user_id = ids[fold(r.username)]


def _ids_by_username(db: Db, usernames: list[str]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for i in range(0, len(usernames), 500):
        part = usernames[i:i + 500]
        for row in db.all("SELECT id, username FROM users WHERE org_id = %s "
                          f"AND username IN ({', '.join(['%s'] * len(part))})", [db.org, *part]):
            ids[fold(row["username"])] = row["id"]
    return ids


def _insert_profiles(db: Db, plan: Plan, rows: list[RowPlan], fields: tuple[str, ...]) -> None:
    if plan.user_type == "student":
        section = plan.section
        db.execute_many(
            f"INSERT INTO student_profiles (org_id, user_id, class_id, section_id, {', '.join(fields)}) "
            f"VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(fields))})",
            [(db.org, r.user_id, section["class_id"], section["section_id"], *(r.values.get(f) for f in fields))
             for r in rows],
        )
    else:
        db.execute_many(
            f"INSERT INTO teacher_profiles (user_id, {', '.join(fields)}) "
            f"VALUES (%s, {', '.join(['%s'] * len(fields))})",
            [(r.user_id, *(r.values.get(f) for f in fields)) for r in rows],
        )


# ------------------------------------------------------------- updating

def _free_moving_numbers(db: Db, updates: list[RowPlan]) -> None:
    """Release roll/admission numbers that change, so values can move
    between students of the same file (swaps) without tripping the unique
    keys half-way."""
    for name in ("roll_no", "admission_no"):
        ids = [r.user_id for r in updates if name in r.changes]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            db.execute(f"UPDATE student_profiles SET {name} = NULL WHERE user_id IN ({', '.join(['%s'] * len(part))})",
                       part)


def _update_users(db: Db, rows: list[RowPlan], photos: dict[str, storage.StoredFile]) -> None:
    changes: dict[int, dict[str, Any]] = {}
    for r in rows:
        values = {f: r.values[f] for f in _USER_FIELDS if f in r.changes}
        photo_path = _photo_path(r, photos)
        if photo_path:
            values["photo_path"] = photo_path
            storage.delete_after_commit(db, r.existing.get("photo_path"))
        if values:
            changes[r.user_id] = values
    _bulk_update(db, "users", "id", changes)


def _upsert_profiles(db: Db, plan: Plan, rows: list[RowPlan], fields: tuple[str, ...]) -> None:
    """Profile rows normally exist; accounts created long ago may lack one,
    hence INSERT … ON DUPLICATE KEY UPDATE with the merged values."""
    if not rows:
        return
    table = "student_profiles" if plan.user_type == "student" else "teacher_profiles"
    lead = ("org_id", "user_id", "class_id", "section_id") if plan.user_type == "student" else ("user_id",)
    columns = (*lead, *fields)
    params = []
    for r in rows:
        head = (db.org, r.user_id, plan.section["class_id"], plan.section["section_id"]) \
            if plan.user_type == "student" else (r.user_id,)
        params.append((*head, *(r.final(f, table) for f in fields)))
    db.execute_many(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
        f"ON DUPLICATE KEY UPDATE {', '.join(f'{f} = VALUES({f})' for f in fields)}",
        params,
    )


def _bulk_update(db: Db, table: str, key: str, changes: dict[int, dict[str, Any]]) -> None:
    """UPDATE t SET col = CASE id WHEN … THEN … ELSE col END … WHERE id IN (…),
    for up to 200 rows per statement. Table/column names come from code."""
    items = list(changes.items())
    for i in range(0, len(items), _UPDATE_CHUNK):
        part = items[i:i + _UPDATE_CHUNK]
        columns = sorted({c for _, values in part for c in values})
        assignments, params = [], []
        for column in columns:
            whens = [(row_key, values[column]) for row_key, values in part if column in values]
            assignments.append(f"{column} = CASE {key} {' '.join(['WHEN %s THEN %s'] * len(whens))} "
                               f"ELSE {column} END")
            params += [p for pair in whens for p in pair]
        keys = [row_key for row_key, _ in part]
        db.execute(f"UPDATE {table} SET {', '.join(assignments)} WHERE {key} IN ({', '.join(['%s'] * len(keys))})",
                   [*params, *keys])


def photo_names(plan: Plan) -> list[str]:
    return [r.values["photo"] for r in _saved(plan.rows) if r.values.get("photo")]


def _saved(rows: Iterable[RowPlan]) -> list[RowPlan]:
    return [r for r in rows if not r.errors and r.action in (CREATE, UPDATE)]
