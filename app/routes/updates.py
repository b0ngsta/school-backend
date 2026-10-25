"""What's new per section, for the mobile app's red "new" dots (any signed-in user).

- GET /updates   newest content time per home-screen section of the caller's role
"""
from fastapi import APIRouter, Depends

from app.db import Db
from app.dependencies import get_current_user, get_db
from app.models.auth import CurrentUser
from app.services import updates as updates_service

router = APIRouter(tags=["updates"])


@router.get("/updates")
def my_updates(user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    """`{server_time, sections: {key: {latest_at, unread?}}}` — see services/updates.py."""
    return updates_service.for_user(db, user)
