"""Opt-in pagination for list endpoints.

Without `?limit=` an endpoint returns the full list, exactly as before (some
endpoints keep their historical cap, e.g. the latest 200 transactions). With
`?limit=N&offset=M` it returns that slice; the route puts the total number of
matching rows in the `X-Total-Count` response header.

Every paginated query orders by a unique column last, so pages never skip or
repeat rows.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.db import Db

MAX_PAGE_SIZE = 500


@dataclass(frozen=True)
class Page:
    limit: int | None = None
    offset: int = 0

    @property
    def requested(self) -> bool:
        return self.limit is not None


@dataclass(frozen=True)
class PageResult:
    rows: list[dict]
    total: int | None  # None when the caller did not ask for a page


def fetch(
    db: Db,
    sql: str,
    params: Sequence[Any],
    order_by: str,
    page: Page | None,
    default_limit: int | None = None,
) -> PageResult:
    """Run `sql` (no ORDER BY / LIMIT) ordered by `order_by`, paginated if asked."""
    params = list(params)
    if page is None or not page.requested:
        suffix = f" ORDER BY {order_by}"
        if default_limit:
            suffix += f" LIMIT {int(default_limit)}"
        return PageResult(db.all(sql + suffix, params), None)

    total = db.scalar(f"SELECT COUNT(*) FROM ({sql}) AS counted", params)
    rows = db.all(f"{sql} ORDER BY {order_by} LIMIT %s OFFSET %s", [*params, page.limit, page.offset])
    return PageResult(rows, int(total or 0))
