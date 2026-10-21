"""Request-scoped database access.

One `Db` per request borrows at most one pooled connection (the auth check
and the endpoint share it). Statements outside `transaction()` run in
autocommit mode; anything that writes more than one row uses

    with db.transaction():
        ...

which commits when the block ends and rolls back if anything raises.
`on_commit` / `on_rollback` hooks keep files on disk consistent with the
database (e.g. delete the old photo only after the new path is committed).

Organisations (schools): once the caller is authenticated, the request's Db
is bound to their organisation (`db.bind_org`, done by get_current_user).
Services read `db.org` and put `org_id = %s` in every query on a
per-school table. Reading `db.org` before anything was bound raises, so a
code path that forgot the binding fails loudly instead of mixing schools.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pymysql

from app.core.errors import DuplicateKey
from app.db.pool import ConnectionPool, PooledConnection

log = logging.getLogger(__name__)

Params = Sequence[Any] | dict[str, Any]

# client/server error codes meaning "this connection is gone"
_DISCONNECT_CODES = frozenset({0, 2006, 2013, 2014, 2045, 2055, 4031})


def _is_disconnect(exc: Exception) -> bool:
    if isinstance(exc, pymysql.err.InterfaceError):
        return True
    return bool(exc.args) and exc.args[0] in _DISCONNECT_CODES


@dataclass(frozen=True)
class ExecResult:
    rowcount: int
    lastrowid: int


class Db:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool
        self._lease: PooledConnection | None = None
        self._used = False
        self._broken = False
        self._depth = 0
        self._on_commit: list[Callable[[], None]] = []
        self._on_rollback: list[Callable[[], None]] = []
        self._org_id: int | None = None

    # ---------------------------------------------------------- organisation

    def bind_org(self, org_id: int) -> None:
        """Scope this session to one organisation (school)."""
        if self._org_id is not None and self._org_id != org_id:
            raise RuntimeError("This database session already belongs to another organisation")
        self._org_id = int(org_id)

    @property
    def org(self) -> int:
        """The organisation this request works in (see bind_org)."""
        if self._org_id is None:
            raise RuntimeError("No organisation bound to this database session")
        return self._org_id

    @property
    def has_org(self) -> bool:
        return self._org_id is not None

    # --------------------------------------------------------------- queries

    def one(self, sql: str, params: Params = ()) -> dict | None:
        def op(cur):
            cur.execute(sql, params)
            return cur.fetchone()
        return self._run(op)

    def all(self, sql: str, params: Params = ()) -> list[dict]:
        def op(cur):
            cur.execute(sql, params)
            return list(cur.fetchall())
        return self._run(op)

    def scalar(self, sql: str, params: Params = ()) -> Any:
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def execute(self, sql: str, params: Params = ()) -> ExecResult:
        def op(cur):
            cur.execute(sql, params)
            return ExecResult(rowcount=cur.rowcount, lastrowid=cur.lastrowid)
        return self._run(op)

    def execute_many(self, sql: str, rows: Sequence[Params]) -> int:
        if not rows:
            return 0

        def op(cur):
            cur.executemany(sql, rows)
            return cur.rowcount
        return self._run(op)

    # ---------------------------------------------------------- transactions

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._depth:  # nested: join the outer transaction
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return

        self._begin()
        conn = self._lease.conn
        self._depth = 1
        try:
            yield
            conn.commit()
        except BaseException:
            self._depth = 0
            self._safe_rollback()
            self._run_hooks(self._on_rollback)
            raise
        self._depth = 0
        self._run_hooks(self._on_commit)

    def on_commit(self, fn: Callable[[], None]) -> None:
        self._require_transaction()
        self._on_commit.append(fn)

    def on_rollback(self, fn: Callable[[], None]) -> None:
        self._require_transaction()
        self._on_rollback.append(fn)

    # -------------------------------------------------------------- lifecycle

    def close(self) -> None:
        if self._lease is not None:
            lease, self._lease = self._lease, None
            self._pool.release(lease, discard=self._broken)

    # --------------------------------------------------------------- internal

    def _connection(self) -> pymysql.connections.Connection:
        if self._lease is None:
            self._lease = self._pool.acquire()
            self._used = False
        return self._lease.conn

    def _run(self, op: Callable[[Any], Any]) -> Any:
        """Run one statement. If a freshly borrowed connection turns out to be
        dead (database restarted, idle connection closed), it is replaced and
        the statement retried once — safe because nothing ran on it yet."""
        while True:
            conn = self._connection()
            retryable = not self._used and not self._depth
            try:
                with conn.cursor() as cur:
                    result = op(cur)
                self._used = True
                return result
            except pymysql.err.IntegrityError as exc:
                if exc.args and exc.args[0] == 1062:
                    raise DuplicateKey.from_mysql_message(str(exc.args[1]) if len(exc.args) > 1 else "") from exc
                raise
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as exc:
                if retryable and _is_disconnect(exc):
                    log.warning("Database connection was lost; reconnecting")
                    self._discard_lease()
                    continue
                self._broken = True  # never hand a dead connection back to the pool
                raise

    def _begin(self) -> None:
        while True:
            conn = self._connection()
            retryable = not self._used
            try:
                conn.begin()
                self._used = True
                return
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as exc:
                if retryable and _is_disconnect(exc):
                    log.warning("Database connection was lost; reconnecting")
                    self._discard_lease()
                    continue
                self._broken = True
                raise

    def _discard_lease(self) -> None:
        lease, self._lease = self._lease, None
        if lease is not None:
            self._pool.release(lease, discard=True)

    def _safe_rollback(self) -> None:
        if self._lease is None:
            return
        try:
            self._lease.conn.rollback()
        except pymysql.MySQLError:
            self._broken = True

    def _require_transaction(self) -> None:
        if not self._depth:
            raise RuntimeError("commit/rollback hooks need an open transaction")

    def _run_hooks(self, hooks: list[Callable[[], None]]) -> None:
        pending = list(hooks)
        self._on_commit.clear()
        self._on_rollback.clear()
        for hook in pending:
            try:
                hook()
            except Exception:
                log.exception("Transaction hook failed")
