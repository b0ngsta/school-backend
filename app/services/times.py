"""TIME columns come back from PyMySQL as `timedelta`; responses use 'HH:MM'."""
from __future__ import annotations

from datetime import time, timedelta


def hhmm(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        minutes = int(value.total_seconds() // 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"
    return str(value)[:5]


def format_row(row: dict | None, *columns: str) -> dict | None:
    """Turn the given TIME columns of a row into 'HH:MM' (in place)."""
    if row:
        for column in columns:
            if column in row:
                row[column] = hhmm(row[column])
    return row
