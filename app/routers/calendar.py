"""School calendar — named events (holiday / exam / event) with marked dates.

- admin / principal / sub_admin create events and mark their dates
- all staff read via GET /calendar; students via GET /student/calendar
- holiday-category events are mirrored into the `holidays` table so the
  existing /holidays endpoints and the mobile Holidays tab stay in sync
- scheduled exams (exams table) are included so their dates show in blue
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import STAFF

router = APIRouter(prefix="/calendar", tags=["calendar"])

editor = require_roles("admin", "principal", "sub_admin")
viewer = require_roles(*STAFF, "driver")

Category = Literal["holiday", "exam", "event"]


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    category: Category = "event"


class EventUpdate(EventCreate):
    dates: list[date] = []


def get_calendar_payload(cur) -> dict:
    """Everything needed to render the calendar (shared with the student portal)."""
    cur.execute("SELECT id, title, category FROM calendar_events ORDER BY title")
    events = cur.fetchall()
    cur.execute("SELECT event_id, date FROM calendar_event_dates ORDER BY date")
    dates = cur.fetchall()
    for e in events:
        e["dates"] = [d["date"] for d in dates if d["event_id"] == e["id"]]
    cur.execute(
        "SELECT e.id, e.name, e.start_date, e.end_date, c.name AS class_name "
        "FROM exams e LEFT JOIN classes c ON c.id = e.class_id ORDER BY e.start_date"
    )
    exams = cur.fetchall()
    # manually added holidays only — event-linked ones already come via events
    cur.execute("SELECT id, date, name FROM holidays WHERE event_id IS NULL ORDER BY date")
    return {"events": events, "exams": exams, "holidays": cur.fetchall()}


def _title_taken(cur, title: str, event_id: int | None = None) -> bool:
    cur.execute(
        "SELECT id FROM calendar_events WHERE title = %s AND id != COALESCE(%s, 0)",
        (title, event_id),
    )
    return bool(cur.fetchone())


def _sync_holidays(cur, event_id: int, body: EventUpdate, actor_id: int):
    """Mirror holiday-category event dates into the legacy holidays table."""
    cur.execute("DELETE FROM holidays WHERE event_id = %s", (event_id,))
    if body.category != "holiday":
        return
    for d in sorted(set(body.dates)):
        cur.execute(
            "INSERT INTO holidays (date, name, event_id, created_by) VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name), event_id = VALUES(event_id)",
            (d, body.title, event_id, actor_id),
        )


@router.get("")
def read_calendar(user: dict = Depends(viewer)):
    with db_cursor() as cur:
        return get_calendar_payload(cur)


@router.post("/events", status_code=201)
def create_event(body: EventCreate, actor: dict = Depends(editor)):
    with db_cursor() as cur:
        if _title_taken(cur, body.title):
            raise HTTPException(status.HTTP_409_CONFLICT, "An event with that name already exists")
        cur.execute(
            "INSERT INTO calendar_events (title, category, created_by) VALUES (%s, %s, %s)",
            (body.title, body.category, actor["id"]),
        )
        return {"id": cur.lastrowid}


@router.put("/events/{event_id}")
def update_event(event_id: int, body: EventUpdate, actor: dict = Depends(editor)):
    """Update title/category and replace the event's full list of dates."""
    with db_cursor() as cur:
        if _title_taken(cur, body.title, event_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "An event with that name already exists")
        cur.execute(
            "UPDATE calendar_events SET title = %s, category = %s WHERE id = %s",
            (body.title, body.category, event_id),
        )
        cur.execute("SELECT id FROM calendar_events WHERE id = %s", (event_id,))
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
        cur.execute("DELETE FROM calendar_event_dates WHERE event_id = %s", (event_id,))
        for d in sorted(set(body.dates)):
            cur.execute(
                "INSERT INTO calendar_event_dates (event_id, date) VALUES (%s, %s)",
                (event_id, d),
            )
        _sync_holidays(cur, event_id, body, actor["id"])
    return {"updated": event_id}


@router.delete("/events/{event_id}")
def delete_event(event_id: int, actor: dict = Depends(editor)):
    """Dates and synced holiday rows are removed by ON DELETE CASCADE."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM calendar_events WHERE id = %s", (event_id,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return {"deleted": event_id}
