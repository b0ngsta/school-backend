"""Environment parsing helpers and the database connection settings.

Kept free of side effects so tools that only need the database (Alembic
migrations, the CLI) can import it without the rest of the app settings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

load_dotenv()


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class DatabaseSettings:
    user: str
    password: str
    database: str
    host: str
    port: int
    unix_socket: str | None
    ssl_ca: str | None
    pool_size: int
    pool_max_overflow: int
    pool_timeout_seconds: int
    pool_recycle_seconds: int
    pool_ping_after_seconds: int
    connect_timeout_seconds: int
    read_timeout_seconds: int


def default_ca_bundle() -> str:
    """System CA bundle: Debian/Ubuntu (Docker, Render), RHEL, then macOS."""
    for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem"):
        if os.path.exists(path):
            return path
    return "/etc/ssl/certs/ca-certificates.crt"


def database_settings() -> DatabaseSettings:
    url = env_str("DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/school_app")
    parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    host = parsed.hostname or "localhost"

    # Local development on macOS connects through the MySQL unix socket;
    # remote databases (TiDB Cloud, the docker `db` service) use TCP.
    socket_path = env_str("MYSQL_SOCKET_PATH", "/tmp/mysql.sock")
    use_socket = bool(socket_path) and host in ("localhost", "127.0.0.1")

    # TLS for remote databases. TiDB Cloud requires it; set DB_SSL_CA="" to
    # turn it off for a database on a private network (docker compose).
    ssl_ca = env_str("DB_SSL_CA", default_ca_bundle()) or None

    return DatabaseSettings(
        user=unquote(parsed.username or "root"),
        password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/"),
        host=host,
        port=parsed.port or 3306,
        unix_socket=socket_path if use_socket else None,
        ssl_ca=None if use_socket else ssl_ca,
        pool_size=env_int("DB_POOL_SIZE", 10, minimum=1),
        pool_max_overflow=env_int("DB_POOL_MAX_OVERFLOW", 10, minimum=0),
        pool_timeout_seconds=env_int("DB_POOL_TIMEOUT", 10, minimum=1),
        pool_recycle_seconds=env_int("DB_POOL_RECYCLE", 1800, minimum=60),
        pool_ping_after_seconds=env_int("DB_POOL_PING_AFTER", 30, minimum=0),
        connect_timeout_seconds=env_int("DB_CONNECT_TIMEOUT", 10, minimum=1),
        read_timeout_seconds=env_int("DB_READ_TIMEOUT", 60, minimum=1),
    )


