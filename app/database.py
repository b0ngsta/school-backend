from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from app.config import DB_CONFIG


def get_connection() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        cursorclass=DictCursor,
        charset="utf8mb4",
        autocommit=False,
    )


@contextmanager
def db_cursor():
    """Yields a dict cursor; commits on success, rolls back on error."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
