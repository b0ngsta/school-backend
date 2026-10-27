"""Admin command line.

    python -m app.cli set-password <username> [--org CODE]   # prompts for the new password
    python -m app.cli revoke-sessions <username> [--org CODE] # sign the user out everywhere
    python -m app.cli list-sessions <username> [--org CODE]
    python -m app.cli check-db                    # connection, TLS and round-trip latency
    python -m app.cli check-data                  # duplicate numbers, extra principals, missing emails
    python -m app.cli copy-uploads-to-s3          # copy UPLOAD_DIR into the S3 bucket
    python -m app.cli check-storage               # write/read/delete a test file in the configured store
    python -m app.cli missing-uploads             # rows whose uploaded file is not in the store (read-only)

Organisations (schools):
    python -m app.cli list-orgs
    python -m app.cli create-org <CODE> "<School name>" [--admin-username admin] [--admin-email x@y.z]
                                                  # prompts for the first admin's password
    python -m app.cli rename-org <CODE> "<New name>"
    python -m app.cli change-org-code <CODE> <NEW-CODE>    # users type the new code from then on
    python -m app.cli disable-org <CODE> | enable-org <CODE>

--org defaults to DEFAULT_ORG_CODE (the school that existed before organisations).

Inside Docker:  docker compose exec api python -m app.cli set-password admin --org XYZPS
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import statistics
import sys
import time

from app.core.config import settings
from app.db import Db, pool
from app.models.common import PASSWORD_MAX_LENGTH
from app.models.organisations import OrganisationCreate, OrganisationUpdate
from app.services import auth as auth_service
from app.services import organisations, sessions


def _org(db: Db, code: str | None) -> dict:
    code = (code or settings.default_org_code or "").strip()
    if not code:
        sys.exit("Name the school with --org CODE")
    org = organisations.find(db, code)
    if not org:
        sys.exit(f"No school with code {code!r} (see `python -m app.cli list-orgs`)")
    db.bind_org(org["id"])
    return org


def _user_id(db: Db, username: str) -> int:
    row = db.one("SELECT id FROM users WHERE org_id = %s AND username = %s", (db.org, username))
    if not row:
        sys.exit(f"No user named {username!r} in this school")
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
    cannot reset a forgotten password). Covers every school."""
    dupes = db.all(
        "SELECT 'admission no' AS kind, TRIM(sp.admission_no) AS value, "
        "GROUP_CONCAT(CONCAT(u.full_name, ' (id ', u.id, ')') SEPARATOR ', ') AS students "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE TRIM(sp.admission_no) <> '' GROUP BY u.org_id, TRIM(sp.admission_no) HAVING COUNT(*) > 1 "
        "UNION ALL "
        "SELECT CONCAT('roll no in section ', sp.section_id), TRIM(sp.roll_no), "
        "GROUP_CONCAT(CONCAT(u.full_name, ' (id ', u.id, ')') SEPARATOR ', ') "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE TRIM(sp.roll_no) <> '' GROUP BY sp.section_id, TRIM(sp.roll_no) HAVING COUNT(*) > 1"
    )
    for d in dupes:
        print(f"{d['kind']} {d['value']!r}: {d['students']}")
    principals = db.all(
        "SELECT o.code, GROUP_CONCAT(CONCAT(u.username, ' (id ', u.id, ')') SEPARATOR ', ') AS names "
        "FROM users u JOIN organisations o ON o.id = u.org_id WHERE u.user_type = 'principal' "
        "GROUP BY o.code HAVING COUNT(*) > 1"
    )
    for p in principals:
        print(f"More than one principal in {p['code']} (only one is allowed): {p['names']}")
    blocking = len(dupes) + len(principals)
    print("Nothing blocks the migrations." if not blocking else f"{blocking} problem(s); fix them before migrating.")

    no_email = db.all(
        "SELECT o.code, u.username, u.user_type FROM users u JOIN organisations o ON o.id = u.org_id "
        "WHERE u.is_active = 1 AND (u.email IS NULL OR TRIM(u.email) = '') ORDER BY o.code, u.user_type, u.username"
    )
    if no_email:
        shown = ", ".join(f"{u['code']}/{u['username']} ({u['user_type']})" for u in no_email[:30])
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


def _describe(store) -> str:
    if store.name == "s3":
        return f"s3://{store.bucket}/{store.prefix}"
    return getattr(store, "root", settings.upload_dir)


def check_storage() -> None:
    """Write, find and delete a small test file in the configured store, so a
    wrong bucket name or key shows up before anyone uploads a photo."""
    from app.core.config import ephemeral_disk
    from app.storage import get_store

    store = get_store()
    where = _describe(store)
    key = f"_storage_check/{secrets.token_hex(8)}.txt"
    try:
        store.put(key, b"storage check", "text/plain")
        found = store.exists(key)
        store.delete(key)
        removed = not store.exists(key)
    except Exception as exc:  # noqa: BLE001 - report any client/credential error plainly
        sys.exit(f"Storage check FAILED ({store.name}, {where}): {exc}")
    if not (found and removed):
        sys.exit(f"Storage check FAILED ({store.name}, {where}): the test file could not be "
                 f"{'found after writing' if not found else 'deleted'}.")
    print(f"Storage OK: {store.name} ({where}) - write, read and delete work.")
    if store.name == "local" and ephemeral_disk():
        print("WARNING: this host's disk is wiped on deploy/restart/sleep. Uploads will disappear; "
              "set STORAGE_BACKEND=s3 (docs/OPERATIONS.md).")


FILE_COLUMNS = ("photo_path", "screenshot_path", "paper_path", "file_path", "attachment_path",
                "image_path", "logo_path", "media_path")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def find_missing_uploads(db: Db, store) -> tuple[int, list[dict]]:
    """Every database row that points at an uploaded file which is not in the
    store (e.g. lost when a host wiped its disk). Read-only.
    Returns (files checked, missing rows)."""
    from app.core.file_urls import relative_upload_path

    columns = db.all(
        "SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND COLUMN_NAME IN (" + ", ".join(["%s"] * len(FILE_COLUMNS)) + ") "
        "ORDER BY TABLE_NAME, COLUMN_NAME", FILE_COLUMNS)
    with_id = {r["t"] for r in db.all(
        "SELECT TABLE_NAME AS t FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND COLUMN_NAME = 'id'")}
    with_org = {r["t"] for r in db.all(
        "SELECT TABLE_NAME AS t FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND COLUMN_NAME = 'org_id'")}
    org_codes = {o["id"]: o["code"] for o in db.all("SELECT id, code FROM organisations")}
    existing = set(store.iter_keys())  # one listing instead of a lookup per file

    checked, missing = 0, []
    for col in columns:
        table, column = col["t"], col["c"]
        if table not in with_id or not (_IDENTIFIER.match(table) and _IDENTIFIER.match(column)):
            continue
        org = "org_id" if table in with_org else "NULL"
        rows = db.all(f"SELECT id, {org} AS org_id, {column} AS path FROM {table} "
                      f"WHERE {column} IS NOT NULL AND {column} <> '' ORDER BY id")
        for r in rows:
            checked += 1
            key = relative_upload_path(r["path"])
            if key is None or key not in existing:
                missing.append({"table": table, "column": column, "id": r["id"],
                                "school": org_codes.get(r["org_id"]), "path": r["path"]})
    return checked, missing


def missing_uploads(db: Db) -> None:
    from app.storage import get_store

    store = get_store()
    checked, missing = find_missing_uploads(db, store)
    for m in missing:
        school = f" [{m['school']}]" if m["school"] else ""
        print(f"{m['table']}.{m['column']} id {m['id']}{school}: {m['path']}")
    print(f"{checked} stored file(s) referenced; {len(missing)} missing from {store.name} ({_describe(store)}).")
    if missing:
        print("Those files are gone; re-upload them (photos, notices, homework, papers...) from the app.")


# ------------------------------------------------------------ organisations

def list_orgs(db: Db) -> None:
    for o in organisations.list_all(db):
        state = "active" if o["is_active"] else "DISABLED"
        print(f"{o['code']:<16} {o['name']:<40} {state:<8} {o['staff']} staff, {o['students']} students")


def create_org(db: Db, code: str, name: str, admin_username: str, admin_email: str | None) -> None:
    password = getpass.getpass(f"Password for the first admin ({admin_username}): ")
    if getpass.getpass("Repeat: ") != password:
        sys.exit("Passwords do not match.")
    try:
        body = OrganisationCreate(code=code, name=name, admin_username=admin_username, admin_password=password,
                                  admin_email=admin_email)
    except ValueError as exc:
        sys.exit(str(exc))
    created = organisations.create(db, body)
    print(f"School {created['code']} ({created['name']}) created. Sign in with school code {created['code']}, "
          f"username {admin_username!r}; the password must be changed at first login.")


def update_org(db: Db, code: str, **changes) -> None:
    org = organisations.update(db, code, OrganisationUpdate(**changes))
    print(f"{org['code']}: {org['name']} ({'active' if org['is_active'] else 'disabled'})")


USER_COMMANDS = {"set-password": set_password, "revoke-sessions": revoke_sessions, "list-sessions": list_sessions}
ORG_COMMANDS = ("list-orgs", "create-org", "rename-org", "change-org-code", "disable-org", "enable-org")


# ----------------------------------------------------------- background jobs

def run_jobs(args) -> None:
    """Run due background jobs (push, email, password-reset lookups) once, or keep going with --forever."""
    import app.main  # noqa: F401  (registers every background task)
    from app.services import background

    if args.forever:
        print("Running background jobs (Ctrl+C to stop)…")
        try:
            background.work_forever()
        except KeyboardInterrupt:
            pass
        return
    total = 0
    while ran := background.run_pending():
        total += ran
    print(f"Ran {total} job(s).")


def retry_failed_jobs(_args) -> None:
    from app.services import background

    print(f"{background.retry_failed()} failed job(s) queued again.")


def cleanup(_args) -> None:
    from app.services import retention

    db = Db(pool)
    try:
        removed = retention.run(db)
    finally:
        db.close()
    for table, count in removed.items():
        print(f"{table:22} {count} row(s) removed")


JOB_COMMANDS = {"run-jobs": run_jobs, "retry-failed-jobs": retry_failed_jobs, "cleanup": cleanup}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=sorted([*USER_COMMANDS, *ORG_COMMANDS, *JOB_COMMANDS, "check-db",
                                                   "check-data", "copy-uploads-to-s3", "check-storage",
                                                   "missing-uploads"]))
    parser.add_argument("args", nargs="*", help="username, or school code [and name] for the *-org commands")
    parser.add_argument("--org", help="school code (default: DEFAULT_ORG_CODE)")
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-email")
    parser.add_argument("--forever", action="store_true", help="run-jobs: keep running (a worker process)")
    args = parser.parse_args()
    if args.command in JOB_COMMANDS:
        try:
            JOB_COMMANDS[args.command](args)
        finally:
            pool.close_all()
        return
    needs = {**dict.fromkeys(USER_COMMANDS, 1), "create-org": 2, "rename-org": 2, "change-org-code": 2,
             "disable-org": 1, "enable-org": 1}
    if len(args.args) < needs.get(args.command, 0):
        parser.error(f"{args.command} needs {needs[args.command]} argument(s)")
    db = Db(pool)
    try:
        if args.command == "check-db":
            check_db(db)
        elif args.command == "check-data":
            check_data(db)
        elif args.command == "copy-uploads-to-s3":
            copy_uploads_to_s3()
        elif args.command == "check-storage":
            check_storage()
        elif args.command == "missing-uploads":
            missing_uploads(db)
        elif args.command == "list-orgs":
            list_orgs(db)
        elif args.command == "create-org":
            create_org(db, args.args[0], args.args[1], args.admin_username, args.admin_email)
        elif args.command == "rename-org":
            update_org(db, args.args[0], name=args.args[1])
        elif args.command == "change-org-code":
            update_org(db, args.args[0], code=args.args[1])
            if args.args[0].strip().upper() == settings.default_org_code:
                print(f"Note: DEFAULT_ORG_CODE is {settings.default_org_code}; set it to the new code so apps "
                      "that send no school code keep signing in to this school.")
        elif args.command in ("disable-org", "enable-org"):
            update_org(db, args.args[0], is_active=args.command == "enable-org")
        else:
            _org(db, args.org)
            USER_COMMANDS[args.command](db, args.args[0])
    finally:
        db.close()
        pool.close_all()


if __name__ == "__main__":
    main()
