"""Small helpers that make revisions safe to run on databases that were
partly upgraded by hand (e.g. migration_v8.sql already applied)."""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


def _scalar(sql: str, **params) -> int:
    return op.get_bind().execute(text(sql), params).scalar() or 0


def has_table(table: str) -> bool:
    return bool(_scalar(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = :t",
        t=table,
    ))


def has_column(table: str, column: str) -> bool:
    return bool(_scalar(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c",
        t=table, c=column,
    ))


def has_index(table: str, index: str) -> bool:
    return bool(_scalar(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i",
        t=table, i=index,
    ))


def add_column(table: str, column: str, definition: str) -> None:
    if not has_column(table, column):
        op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def add_index(table: str, index: str, columns: str, unique: bool = False) -> None:
    if not has_index(table, index):
        kind = "UNIQUE INDEX" if unique else "INDEX"
        op.execute(f"ALTER TABLE {table} ADD {kind} {index} ({columns})")


def drop_index(table: str, index: str) -> None:
    if has_index(table, index):
        op.execute(f"ALTER TABLE {table} DROP INDEX {index}")
