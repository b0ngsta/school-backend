"""School calendar — named events (holiday / exam / event) with marked dates.

- admin / principal / sub_admin create events and mark their dates
- holiday-category events are mirrored into the `holidays` table so the
  /holidays endpoints and the mobile Holidays tab stay in sync
- scheduled exams (exams table) are included so their dates show up
"""
from __future__ import annotations

from app.core.errors import Conflict, DuplicateKey, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.calendar import EventCreate, EventUpdate


def get_calendar_payload(db: Db) -> dict:
    """Everything needed to render the calendar (shared with the student portal)."""
    events = db.all("SELECT id, title, category FROM calendar_events ORDER BY title")
    dates_by_event: dict[int, list] = {}
    for d in db.all("SELECT event_id, date FROM calendar_event_dates ORDER BY date"):
        dates_by_event.setdefault(d["event_id"], []).append(d["date"])
    for e in events:
        e["dates"] = dates_by_event.get(e["id"], [])
    exams = db.all(
        "SELECT e.id, e.name, e.start_date, e.end_date, c.name AS class_name "
        "FROM exams e LEFT JOIN classes c ON c.id = e.class_id ORDER BY e.start_date"
    )
    # manually added holidays only — event-linked ones already come via events
    holidays = db.all("SELECT id, date, name FROM holidays WHERE event_id IS NULL ORDER BY date")
    return {"events": events, "exams": exams, "holidays": holidays}


def _title_taken(db: Db, title: str, event_id: int | None = None) -> bool:
    return db.one(
        "SELECT id FROM calendar_events WHERE title = %s AND id != COALESCE(%s, 0)", (title, event_id)
    ) is not None


def _sync_holidays(db: Db, event_id: int, body: EventUpdate, actor_id: int) -> None:
    """Mirror holiday-category event dates into the holidays table."""
    db.execute("DELETE FROM holidays WHERE event_id = %s", (event_id,))
    if body.category != "holiday":
        return
    db.execute_many(
        "INSERT INTO holidays (date, name, event_id, created_by) VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE name = VALUES(name), event_id = VALUES(event_id)",
        [(d, body.title, event_id, actor_id) for d in sorted(set(body.dates))],
    )


def create_event(db: Db, actor: CurrentUser, body: EventCreate) -> dict:
    if _title_taken(db, body.title):
        raise Conflict("An event with that name already exists")
    try:
        event_id = db.execute(
            "INSERT INTO calendar_events (title, category, created_by) VALUES (%s, %s, %s)",
            (body.title, body.category, actor.id),
        ).lastrowid
    except DuplicateKey:
        raise Conflict("An event with that name already exists") from None
    return {"id": event_id}


def update_event(db: Db, actor: CurrentUser, event_id: int, body: EventUpdate) -> dict:
    """Update title/category and replace the event's full list of dates."""
    with db.transaction():
        if _title_taken(db, body.title, event_id):
            raise Conflict("An event with that name already exists")
        if not db.one("SELECT id FROM calendar_events WHERE id = %s FOR UPDATE", (event_id,)):
            raise NotFound("Event not found")
        db.execute("UPDATE calendar_events SET title = %s, category = %s WHERE id = %s",
                   (body.title, body.category, event_id))
        db.execute("DELETE FROM calendar_event_dates WHERE event_id = %s", (event_id,))
        db.execute_many(
            "INSERT INTO calendar_event_dates (event_id, date) VALUES (%s, %s)",
            [(event_id, d) for d in sorted(set(body.dates))],
        )
        _sync_holidays(db, event_id, body, actor.id)
    return {"updated": event_id}


def delete_event(db: Db, event_id: int) -> dict:
    """Dates and synced holiday rows are removed by ON DELETE CASCADE."""
    if db.execute("DELETE FROM calendar_events WHERE id = %s", (event_id,)).rowcount == 0:
        raise NotFound("Event not found")
    return {"deleted": event_id}
