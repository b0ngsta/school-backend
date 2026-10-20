"""Notification inbox (any signed-in user, students included) and push-device
registration.

- /notifications   the caller's notifications, newest first (+ unread count, mark read)
- /devices         FCM tokens: register after sign-in, remove after sign-out
"""
from fastapi import APIRouter, Depends, Path, Response

from app.db import Db
from app.dependencies import get_current_user, get_db, page_params, paged
from app.models.auth import CurrentUser
from app.models.notifications import DeviceRegister
from app.services import notifications as notification_service
from app.services.pagination import Page

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
def list_notifications(response: Response, page: Page = Depends(page_params),
                       user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    return paged(response, notification_service.list_notifications(db, user, page))


@router.get("/notifications/unread-count")
def unread_count(user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    return notification_service.unread_count(db, user)


@router.put("/notifications/read-all", status_code=204)
def mark_all_read(user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    notification_service.mark_all_read(db, user)
    return Response(status_code=204)


@router.put("/notifications/{notification_id}/read", status_code=204)
def mark_read(notification_id: int, user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    notification_service.mark_read(db, user, notification_id)
    return Response(status_code=204)


@router.post("/devices", status_code=204)
def register_device(body: DeviceRegister, user: CurrentUser = Depends(get_current_user),
                    db: Db = Depends(get_db)):
    notification_service.register_device(db, user, body.token, body.platform)
    return Response(status_code=204)


@router.delete("/devices/{token:path}", status_code=204)
def unregister_device(token: str = Path(max_length=512), db: Db = Depends(get_db)):
    """No sign-in needed: the app calls it right after signing out, and knowing
    the token is enough."""
    notification_service.unregister_device(db, token)
    return Response(status_code=204)
