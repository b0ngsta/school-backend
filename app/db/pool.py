"""A small thread-safe PyMySQL connection pool.

Why it exists: opening a TLS connection to a remote database costs several
network round trips. The pool keeps connections open between requests.

Connections that sat idle (e.g. the app was quiet for hours and the database
closed them) are pinged before reuse and transparently replaced, so the first
request after a long pause works like any other.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import pymysql
from pymysql.constants import SERVER_STATUS
from pymysql.cursors import DictCursor

from app.core.clock import mysql_offset
from app.core.config import DatabaseSettings
from app.core.errors import ServiceUnavailable

log = logging.getLogger(__name__)


@dataclass
class PooledConnection:
    conn: pymysql.connections.Connection
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)


class ConnectionPool:
    def __init__(self, config: DatabaseSettings):
        self._config = config
        self._idle: deque[PooledConnection] = deque()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(config.pool_size + config.pool_max_overflow)

    # -------------------------------------------------------------- public

    def acquire(self) -> PooledConnection:
        if not self._slots.acquire(timeout=self._config.pool_timeout_seconds):
            log.error("Database pool exhausted (%s connections busy)",
                      self._config.pool_size + self._config.pool_max_overflow)
            raise ServiceUnavailable("Server is busy, please retry")
        try:
            return self._checkout()
        except BaseException:
            self._slots.release()
            raise

    def release(self, item: PooledConnection, *, discard: bool = False) -> None:
        try:
            if not discard and self._in_transaction(item.conn):
                try:
                    item.conn.rollback()
                except pymysql.MySQLError:
                    discard = True
            with self._lock:
                keep = not discard and item.conn.open and len(self._idle) < self._config.pool_size
                if keep:
                    item.last_used_at = time.monotonic()
                    self._idle.append(item)
            if not keep:
                self._close(item)
        finally:
            self._slots.release()

    def close_all(self) -> None:
        with self._lock:
            items, self._idle = list(self._idle), deque()
        for item in items:
            self._close(item)

    # ------------------------------------------------------------ internal

    def _checkout(self) -> PooledConnection:
        while True:
            with self._lock:
                item = self._idle.pop() if self._idle else None
            if item is None:
                return PooledConnection(self._connect())
            if self._is_usable(item):
                return item
            self._close(item)

    def _is_usable(self, item: PooledConnection) -> bool:
        now = time.monotonic()
        if now - item.created_at > self._config.pool_recycle_seconds:
            return False
        if now - item.last_used_at > self._config.pool_ping_after_seconds:
            try:
                item.conn.ping(reconnect=False)
            except pymysql.MySQLError:
                return False
        return True

    def _connect(self) -> pymysql.connections.Connection:
        c = self._config
        kwargs: dict = {
            "user": c.user,
            "password": c.password,
            "database": c.database,
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": True,
            "connect_timeout": c.connect_timeout_seconds,
            "read_timeout": c.read_timeout_seconds,
            "write_timeout": c.read_timeout_seconds,
            # CURDATE()/NOW() and TIMESTAMP columns follow the school timezone
            "init_command": f"SET time_zone = '{mysql_offset()}'",
        }
        if c.unix_socket:
            kwargs["unix_socket"] = c.unix_socket
        else:
            kwargs["host"] = c.host
            kwargs["port"] = c.port
            if c.ssl_ca:
                kwargs["ssl_ca"] = c.ssl_ca
                kwargs["ssl_verify_cert"] = True
                kwargs["ssl_verify_identity"] = True
        try:
            return pymysql.connect(**kwargs)
        except pymysql.err.OperationalError as exc:
            log.error("Cannot connect to the database: %s", exc)
            raise ServiceUnavailable("Database is unavailable, please retry") from exc

    @staticmethod
    def _in_transaction(conn: pymysql.connections.Connection) -> bool:
        return bool(conn.server_status & SERVER_STATUS.SERVER_STATUS_IN_TRANS)

    @staticmethod
    def _close(item: PooledConnection) -> None:
        try:
            item.conn.close()
        except Exception:  # already closed / broken socket
            pass
