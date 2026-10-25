"""Alembic migrations: fresh installs, adopting legacy databases, safety checks."""
import re
import uuid

import pymysql
import pytest
from conftest import TEST_DB_URL
from dbutil import ROOT, alembic, drop_database, recreate_database, run_sql_file, with_database


@pytest.fixture
def scratch_db():
    """A throwaway database next to the test database."""
    url = with_database(TEST_DB_URL, f"mig_{uuid.uuid4().hex[:10]}")
    recreate_database(url)
    yield url
    drop_database(url)


def _query(url, sql):
    name = url.rsplit("/", 1)[1].split("?")[0]
    conn = pymysql.connect(**_conn_kwargs(url), database=name)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


def _conn_kwargs(url):
    from urllib.parse import urlparse
    p = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    return {"host": p.hostname, "port": p.port or 3306, "user": p.username, "password": p.password or ""}


def _schema(url) -> dict[str, list[str]]:
    out = {}
    for (table,) in _query(url, "SHOW TABLES"):
        ddl = _query(url, f"SHOW CREATE TABLE `{table}`")[0][1]
        ddl = re.sub(r" AUTO_INCREMENT=\d+", "", ddl).replace("CHARACTER SET utf8mb4 ", "")
        ddl = re.sub(r"CONSTRAINT `[^`]+` ", "CONSTRAINT ", ddl)
        out[table] = sorted(line.strip().rstrip(",") for line in ddl.splitlines()[1:-1])
    return out


def test_fresh_install_creates_everything(scratch_db):
    alembic(scratch_db)
    tables = {t for (t,) in _query(scratch_db, "SHOW TABLES")}
    assert {"users", "auth_sessions", "audit_logs", "calendar_events", "alembic_version"} <= tables
    assert _query(scratch_db, "SELECT COUNT(*) FROM users WHERE must_change_password = 1")[0][0] == 5
    assert _query(scratch_db, "SELECT version_num FROM alembic_version")[0][0] == "0011_student_portal"
    assert _query(scratch_db, "SELECT code FROM organisations")[0][0] == "DEFAULT"
    assert _query(scratch_db, "SELECT COUNT(*) FROM users WHERE org_id = 1")[0][0] == 5


def test_legacy_schema_is_adopted_and_ends_identical(scratch_db):
    fresh = with_database(TEST_DB_URL, scratch_db.rsplit("/", 1)[1] + "_f")
    recreate_database(fresh)
    try:
        alembic(fresh)
        name = scratch_db.rsplit("/", 1)[1]
        run_sql_file(scratch_db, ROOT / "migrations" / "legacy" / "schema_v8.sql", replace_db=name)
        alembic(scratch_db)  # baseline adopts it; v8 is skipped; unique keys are added
        assert _schema(scratch_db) == _schema(fresh)
    finally:
        drop_database(fresh)


def test_upgrade_is_repeatable_and_reversible(scratch_db):
    alembic(scratch_db)
    alembic(scratch_db)  # no-op
    alembic(scratch_db, "downgrade", "0001_baseline_v7")
    assert not _query(scratch_db, "SHOW TABLES LIKE 'auth_sessions'")
    alembic(scratch_db)
    assert _query(scratch_db, "SHOW TABLES LIKE 'auth_sessions'")


def test_duplicate_student_numbers_stop_the_migration(scratch_db):
    alembic(scratch_db, "upgrade", "0002_auth_audit_v8")
    name = scratch_db.rsplit("/", 1)[1]
    conn = pymysql.connect(**_conn_kwargs(scratch_db), database=name, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO classes (name) VALUES ('C1')")
        cur.execute("INSERT INTO sections (class_id, name) VALUES (LAST_INSERT_ID(), 'A')")
        section = cur.lastrowid
        for n in (1, 2):
            cur.execute("INSERT INTO users (username, password_hash, full_name, user_type) "
                        "VALUES (%s, 'x', %s, 'student')", (f"s{n}", f"Kid {n}"))
            cur.execute("INSERT INTO student_profiles (user_id, section_id, roll_no, admission_no) "
                        "VALUES (LAST_INSERT_ID(), %s, '7', %s)", (section, "A-1" if n == 1 else " A-1 "))
    conn.close()
    with pytest.raises(RuntimeError, match=r"admission no 'A-1'.*Kid 1.*Kid 2"):
        alembic(scratch_db)
    assert _query(scratch_db, "SELECT version_num FROM alembic_version")[0][0] == "0002_auth_audit_v8"


def test_second_principal_stops_the_migration(scratch_db):
    alembic(scratch_db, "upgrade", "0003_student_unique_keys")
    name = scratch_db.rsplit("/", 1)[1]
    conn = pymysql.connect(**_conn_kwargs(scratch_db), database=name, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, full_name, user_type) "
                    "VALUES ('vp', 'x', 'Vice Principal', 'principal')")
    conn.close()
    with pytest.raises(RuntimeError, match=r"(?s)Only one principal.*Principal.*Vice Principal"):
        alembic(scratch_db)
    assert _query(scratch_db, "SELECT version_num FROM alembic_version")[0][0] == "0003_student_unique_keys"


def test_database_refuses_a_second_principal(scratch_db):
    alembic(scratch_db)
    name = scratch_db.rsplit("/", 1)[1]
    conn = pymysql.connect(**_conn_kwargs(scratch_db), database=name, autocommit=True)
    try:
        with conn.cursor() as cur, pytest.raises(pymysql.err.IntegrityError, match="uq_users_org_principal"):
            cur.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
                        "VALUES (1, 'vp', 'x', 'Vice Principal', 'principal')")
        with conn.cursor() as cur:  # any number of other roles is fine
            cur.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
                        "VALUES (1, 't1', 'x', 'T', 'teacher'), (1, 't2', 'x', 'T', 'teacher')")
        with conn.cursor() as cur:  # and another school has its own principal
            cur.execute("INSERT INTO organisations (code, name) VALUES ('ABC', 'ABC School')")
            cur.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
                        "VALUES (LAST_INSERT_ID(), 'principal', 'x', 'P', 'principal')")
    finally:
        conn.close()


def test_existing_school_becomes_organisation_one(scratch_db):
    """A database in use before organisations: every row lands in organisation 1,
    and the unique rules become per school."""
    alembic(scratch_db, "upgrade", "0008_class_homework")
    name = scratch_db.rsplit("/", 1)[1]
    conn = pymysql.connect(**_conn_kwargs(scratch_db), database=name, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO classes (name) VALUES ('C1')")
        cur.execute("INSERT INTO sections (class_id, name) VALUES (LAST_INSERT_ID(), 'A')")
        cur.execute("INSERT INTO holidays (date, name, created_by) VALUES ('2026-01-26', 'Republic Day', 1)")
    conn.close()
    alembic(scratch_db)
    for table in ("users", "classes", "sections", "holidays", "calendar_events"):
        assert _query(scratch_db, f"SELECT COUNT(*) FROM {table} WHERE org_id <> 1")[0][0] == 0
        assert _query(scratch_db, f"SELECT COUNT(*) FROM {table}")[0][0] > 0
    conn = pymysql.connect(**_conn_kwargs(scratch_db), database=name, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO organisations (code, name) VALUES ('ABC', 'ABC School')")
            org = cur.lastrowid
            # the same names in another school are fine …
            cur.execute("INSERT INTO classes (org_id, name) VALUES (%s, 'C1')", (org,))
            cur.execute("INSERT INTO users (org_id, username, password_hash, full_name, user_type) "
                        "VALUES (%s, 'admin', 'x', 'Admin', 'admin')", (org,))
            cur.execute("INSERT INTO holidays (org_id, date, name, created_by) "
                        "VALUES (%s, '2026-01-26', 'Republic Day', LAST_INSERT_ID())", (org,))
        with conn.cursor() as cur, pytest.raises(pymysql.err.IntegrityError, match="uq_classes_org_name"):
            cur.execute("INSERT INTO classes (org_id, name) VALUES (%s, 'C1')", (org,))  # … but not twice in one
        with conn.cursor() as cur, pytest.raises(pymysql.err.OperationalError, match="org_id"):
            cur.execute("INSERT INTO notices (created_by, title, body) VALUES (1, 't', 'b')")  # no school: refused
    finally:
        conn.close()
