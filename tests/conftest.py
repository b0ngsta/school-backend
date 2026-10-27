"""Test setup.

The suite runs against a real MySQL 8 / TiDB database. Point it at an EMPTY
throwaway database that the user may drop and recreate:

    export TEST_DATABASE_URL="mysql+pymysql://root:pass@127.0.0.1:3306/school_test"
    pytest

The database is created once per run with `alembic upgrade head` (so every
run also tests the migrations); each test then starts from the freshly
migrated state (all rows removed, seeded accounts restored). The database
user needs permission to create and drop that database.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # for `migrations.*` imports inside Alembic revisions
TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

if not TEST_DB_URL:
    raise pytest.UsageError(
        "Set TEST_DATABASE_URL to an empty throwaway MySQL database, e.g. "
        "mysql+pymysql://root:pass@127.0.0.1:3306/school_test"
    )

# Settings are read at import time, so configure the environment first.
os.environ.update(
    APP_ENV="development",
    DATABASE_URL=TEST_DB_URL,
    MYSQL_SOCKET_PATH="",
    DB_SSL_CA=os.getenv("TEST_DB_SSL_CA", ""),
    SECRET_KEY="test-secret-key-0123456789-abcdefghijklmnop",
    UPLOAD_DIR=tempfile.mkdtemp(prefix="school-uploads-"),
    APP_TIMEZONE="Asia/Kolkata",
    ACCESS_TOKEN_EXPIRE_MINUTES="15",
    REFRESH_REUSE_GRACE_SECONDS="60",
    DB_POOL_PING_AFTER="0",
    PLATFORM_SECRET="test-platform-secret",
    DEFAULT_ORG_CODE="DEFAULT",
    JOBS_WORKER="false",  # tests run jobs inline (background.run_inline) or call run_pending() themselves
)

from dbutil import alembic, recreate_database  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Db, pool  # noqa: E402
from app.main import app  # noqa: E402
from app.services import background  # noqa: E402

background.run_inline = True  # after-response jobs finish before assertions run

_SEEDED_TABLES = ("organisations", "users", "calendar_events")


def _stored_columns(db: Db, table: str) -> list[str]:
    """Columns that can be written (generated columns are computed by MySQL)."""
    return [r["COLUMN_NAME"] for r in db.all(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND EXTRA NOT LIKE %s "
        "ORDER BY ORDINAL_POSITION", (table, "%GENERATED%"))]


@pytest.fixture(scope="session")
def migrated_db():
    """Create the test database with Alembic and remember the seeded rows."""
    recreate_database(TEST_DB_URL)
    alembic(TEST_DB_URL, "upgrade", "head")
    db = Db(pool)
    try:
        tables = [r["TABLE_NAME"] for r in db.all(
            "SELECT TABLE_NAME FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND TABLE_NAME <> 'alembic_version'")]
        seeds = {t: db.all(f"SELECT {', '.join(_stored_columns(db, t))} FROM {t}") for t in _SEEDED_TABLES}
    finally:
        db.close()
    return tables, seeds


@pytest.fixture(autouse=True)
def fresh_db(migrated_db):
    """Reset every table to the state right after migrating."""
    tables, seeds = migrated_db
    db = Db(pool)
    try:
        db.execute("SET FOREIGN_KEY_CHECKS = 0")
        with db.transaction():
            for table in tables:
                db.execute(f"DELETE FROM {table}")
            for table, rows in seeds.items():
                if rows:
                    columns = list(rows[0])
                    db.execute_many(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                        [tuple(r[c] for c in columns) for r in rows],
                    )
    finally:
        db.execute("SET FOREIGN_KEY_CHECKS = 1")
        db.close()
    yield


@pytest.fixture(autouse=True)
def known_captcha(monkeypatch):
    """Every CAPTCHA picture shows helpers.CAPTCHA_TEXT, so tests can solve it."""
    from app.services import captcha

    monkeypatch.setattr(captcha, "new_text", lambda: "ABCDE")


@pytest.fixture
def db():
    """A session bound to the default school (organisation 1), like a signed-in request."""
    session = Db(pool)
    session.bind_org(1)
    yield session
    session.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def school(client):
    from helpers import School

    return School(client)
