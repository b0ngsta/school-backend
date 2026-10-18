"""Admin command line.

    python -m app.cli set-password <username>     # prompts for the new password
    python -m app.cli revoke-sessions <username>  # sign the user out everywhere
    python -m app.cli list-sessions <username>
    python -m app.cli check-db                    # connection, TLS and round-trip latency
    python -m app.cli check-data                  # duplicate numbers, extra principals, missing emails
    python -m app.cli copy-uploads-to-s3          # copy UPLOAD_DIR into the S3 bucket

Inside Docker:  docker compose exec api python -m app.cli set-password admin
"""
from __future__ import annotations

import argparse
import getpass
import os
import statistics
import sys
import time

from app.core.config import settings
from app.db import Db, pool
from app.models.common import PASSWORD_MAX_LENGTH
from app.services import auth as auth_service
from app.services import sessions


def _user_id(db: Db, username: str) -> int:
    row = db.one("SELECT id FROM users WHERE username = %s", (username,))
    if not row:
        sys.exit(f"No user named {username!r}")
    return row["id"]


def set_password(db: Db, username: str) -> None:
    user_id = _user_id(db, username)
    password = getpass.getpass("New password: ")
    if len(password) < 8 or len(password) > PASSWORD_MAX_LENGTH:
        sys.exit("Use 8 to 128 characters.")
    if getpass.getpass("Repeat: ") != password:
        sys.exit("Passwords do not match.")
    with db.transaction():
        auth_service.set_password(db, user_id, password, reason="password_reset_cli")
    print(f"Password for {username!r} changed; all of its sessions were signed out.")


def revoke_sessions(db: Db, username: str) -> None:
    user_id = _user_id(db, username)
    with db.transaction():
        db.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user_id,))
        sessions.revoke_all(db, user_id, "revoked_cli")
    print(f"All sessions of {username!r} revoked.")


def list_sessions(db: Db, username: str) -> None:
    rows = db.all(
        "SELECT id, created_at, last_used_at, expires_at, revoked_at, revoke_reason, ip, user_agent "
        "FROM auth_sessions WHERE user_id = %s ORDER BY last_used_at DESC",
        (_user_id(db, username),),
    )
    for r in rows:
        state = f"revoked ({r['revoke_reason']})" if r["revoked_at"] else f"active until {r['expires_at']} UTC"
        print(f"{r['id'][:8]}…  last used {r['last_used_at']} UTC  {state}  {r['ip'] or ''}  {r['user_agent'] or ''}")
    if not rows:
        print("No sessions.")


def check_db(db: Db) -> None:
    version = db.scalar("SELECT VERSION()")
    tls = db.one("SHOW STATUS LIKE 'Ssl_cipher'")
    print(f"Connected: {version}; TLS cipher: {(tls or {}).get('Value') or 'none'}; "
          f"session time zone {db.scalar('SELECT @@session.time_zone')}")
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        db.scalar("SELECT 1")
        timings.append((time.perf_counter() - start) * 1000)
    median = statistics.median(timings)
    print(f"Round trip to the database: median {median:.1f} ms, worst {max(timings):.1f} ms")
    if median > 10:
        print("  A typical request makes 2-5 round trips. Above ~10 ms the app and the database are\n"
              "  probably in different regions; host them in the same region (or move the database).")


def check_data(db: Db) -> None:
    """Report data that blocks a migration (duplicate student numbers, more
    than one principal) or a feature (accounts without an email address
    cannot reset a forgotten password)."""
    dupes = db.all(
        "SELECT 'admission no' AS kind, TRIM(sp.admission_no) AS value, "
        "GROUP_CONCAT(CONCAT(u.full_name, ' (id ', u.id, ')') SEPARATOR ', ') AS students "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE TRIM(sp.admission_no) <> '' GROUP BY TRIM(sp.admission_no) HAVING COUNT(*) > 1 "
        "UNION ALL "
        "SELECT CONCAT('roll no in section ', sp.section_id), TRIM(sp.roll_no), "
        "GROUP_CONCAT(CONCAT(u.full_name, ' (id ', u.id, ')') SEPARATOR ', ') "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE TRIM(sp.roll_no) <> '' GROUP BY sp.section_id, TRIM(sp.roll_no) HAVING COUNT(*) > 1"
    )
    for d in dupes:
        print(f"{d['kind']} {d['value']!r}: {d['students']}")
    principals = db.all("SELECT id, username, full_name FROM users WHERE user_type = 'principal' ORDER BY id")
    if len(principals) > 1:
        print("More than one principal (only one is allowed): "
              + ", ".join(f"{p['username']} (id {p['id']})" for p in principals))
    blocking = len(dupes) + (len(principals) > 1)
    print("Nothing blocks the migrations." if not blocking else f"{blocking} problem(s); fix them before migrating.")

    no_email = db.all(
        "SELECT username, user_type FROM users WHERE is_active = 1 AND (email IS NULL OR TRIM(email) = '') "
        "ORDER BY user_type, username"
    )
    if no_email:
        shown = ", ".join(f"{u['username']} ({u['user_type']})" for u in no_email[:30])
        more = f" and {len(no_email) - 30} more" if len(no_email) > 30 else ""
        print(f"{len(no_email)} active account(s) have no email and cannot reset a forgotten password: "
              f"{shown}{more}")


def copy_uploads(source, target) -> tuple[int, int]:
    """Copy every file from one store to another. Returns (copied, skipped)."""
    from app.services.storage import _MEDIA_TYPES

    copied = skipped = 0
    for key in source.iter_keys():
        if target.exists(key):
            skipped += 1
            continue
        with open(source.path_for(key), "rb") as fh:
            data = fh.read()
        target.put(key, data, _MEDIA_TYPES.get(os.path.splitext(key)[1].lower(), "application/octet-stream"))
        copied += 1
    return copied, skipped


def copy_uploads_to_s3() -> None:
    from app.storage.local import LocalStore
    from app.storage.s3 import S3Store

    if settings.storage_backend != "s3":
        sys.exit("Set STORAGE_BACKEND=s3 and the S3_* settings first.")
    copied, skipped = copy_uploads(LocalStore(settings.upload_dir), S3Store.from_settings(settings))
    print(f"Copied {copied} file(s) to s3://{settings.s3_bucket}; {skipped} already there. "
          "Local files were left in place.")


USER_COMMANDS = {"set-password": set_password, "revoke-sessions": revoke_sessions, "list-sessions": list_sessions}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=sorted([*USER_COMMANDS, "check-db", "check-data", "copy-uploads-to-s3"]))
    parser.add_argument("username", nargs="?")
    args = parser.parse_args()
    if args.command in USER_COMMANDS and not args.username:
        parser.error(f"{args.command} needs a username")
    db = Db(pool)
    try:
        if args.command == "check-db":
            check_db(db)
        elif args.command == "check-data":
            check_data(db)
        elif args.command == "copy-uploads-to-s3":
            copy_uploads_to_s3()
        else:
            USER_COMMANDS[args.command](db, args.username)
    finally:
        db.close()
        pool.close_all()


if __name__ == "__main__":
    main()
