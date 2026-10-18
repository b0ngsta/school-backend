"""Database helpers for the test-suite: create throwaway databases and run
the Alembic migrations against them."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import pymysql
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def server_connection(url: str) -> pymysql.connections.Connection:
    p = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    return pymysql.connect(host=p.hostname, port=p.port or 3306, user=p.username,
                           password=p.password or "", autocommit=True)


def database_name(url: str) -> str:
    return urlparse(url).path.lstrip("/")


def with_database(url: str, name: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{name}\\1", url, count=1)


def recreate_database(url: str) -> None:
    name = database_name(url)
    conn = server_connection(url)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
            cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        conn.close()


def drop_database(url: str) -> None:
    conn = server_connection(url)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{database_name(url)}`")
    finally:
        conn.close()


@contextmanager
def database_url_env(url: str):
    """Point Alembic (which reads DATABASE_URL) at another database."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def alembic(url: str, action: str = "upgrade", revision: str = "head") -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    with database_url_env(url):
        getattr(command, action)(cfg, revision)


def run_sql_file(url: str, path: Path, replace_db: str | None = None) -> None:
    sql = path.read_text()
    if replace_db:
        sql = re.sub(r"\bschool_app\b", replace_db, sql)
    p = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    conn = pymysql.connect(host=p.hostname, port=p.port or 3306, user=p.username, password=p.password or "",
                           client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            while cur.nextset():
                pass
    finally:
        conn.close()
