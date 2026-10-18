"""Audit trail for sensitive actions (who changed marks, fees, accounts…).

Rows are written inside the caller's transaction, so an action and its audit
entry are committed (or rolled back) together.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.context import client_ip, request_id
from app.db import Db
from app.models.auth import CurrentUser


def record(
    db: Db,
    actor: CurrentUser | int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    actor_id = actor.id if isinstance(actor, CurrentUser) else actor
    db.execute(
        "INSERT INTO audit_logs (actor_id, action, entity_type, entity_id, details, ip, request_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            actor_id,
            action,
            entity_type,
            None if entity_id is None else str(entity_id),
            json.dumps(details, default=str) if details else None,
            client_ip(),
            request_id(),
        ),
    )
