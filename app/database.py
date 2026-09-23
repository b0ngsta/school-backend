from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from app.config import DB_CONFIG


def get_connection() -> pymysql.connections.Connection:
    connection_kwargs = {
        "user": DB_CONFIG["user"],
        "password": DB_CONFIG["password"],
        "database": DB_CONFIG["database"],
        "cursorclass": DictCursor,
        "charset": "utf8mb4",
        "autocommit": False,
    }

    if DB_CONFIG.get("unix_socket"):
        connection_kwargs["unix_socket"] = DB_CONFIG["unix_socket"]
    else:
        connection_kwargs["host"] = DB_CONFIG.get("host", "localhost")
        connection_kwargs["port"] = DB_CONFIG.get("port", 3306)

    return pymysql.connect(**connection_kwargs)


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
