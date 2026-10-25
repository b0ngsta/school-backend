"""School events — managers create / edit; everyone signed in can see them.

- GET    /events?status=upcoming|completed   (paged with ?limit=&offset=)
- GET    /events/{id}
- POST   /events · PUT /events/{id} · DELETE /events/{id}
- POST   /events/{id}/photos    multipart: file, caption?
- POST   /events/{id}/videos    JSON: url (https link), caption?
- DELETE /events/{id}/media/{media_id}
"""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_current_user, get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.models.events import EventSave, EventVideo
from app.services import events as service
from app.services.pagination import Page

router = APIRouter(prefix="/events", tags=["events"])

manager = require_roles(*MANAGERS)


@router.get("")
def list_events(response: Response, status: Literal["upcoming", "completed"] | None = None,
                page: Page = Depends(page_params), user: CurrentUser = Depends(get_current_user),
                db: Db = Depends(get_db)):
    return paged(response, service.list_events(db, status, page))


@router.get("/{event_id}")
def get_event(event_id: int, user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    return service.get(db, event_id)


@router.post("", status_code=201)
def create_event(body: EventSave, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.create(db, actor, body)


@router.put("/{event_id}")
def update_event(event_id: int, body: EventSave, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.update(db, actor, event_id, body)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    service.delete(db, actor, event_id)
    return Response(status_code=204)


@router.post("/{event_id}/photos", status_code=201)
def add_photo(event_id: int, file: UploadFile = File(...), caption: str | None = Form(None),
              actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.add_photo(db, actor, event_id, file, caption)


@router.post("/{event_id}/videos", status_code=201)
def add_video(event_id: int, body: EventVideo, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.add_video(db, actor, event_id, body)


@router.delete("/{event_id}/media/{media_id}", status_code=204)
def delete_media(event_id: int, media_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    service.delete_media(db, actor, event_id, media_id)
    return Response(status_code=204)
