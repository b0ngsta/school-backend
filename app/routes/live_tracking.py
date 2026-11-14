"""Live bus tracking (services/live_tracking.py).

- POST /driver/location               the driver's phone: the fixes since the last upload (≤ 20)
- GET  /driver/tracking/config        the driver's app: windows, rates, whether to share now
- POST /driver/tracking/token         a key for the phone's location service (upload only)
- GET  /student/transport/live        parents: where the child's bus is (and "I am watching")
- POST /student/transport/live/stop   parents: the Track screen closed (best effort)

Buses are tracked only inside the school's windows (see the service).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.core.errors import FeatureDisabled, Forbidden, Unauthorized
from app.db import Db
from app.dependencies import _bearer, get_current_user, get_db, require_feature, require_roles
from app.models.auth import CurrentUser
from app.models.live_tracking import LocationBatch
from app.services import live_tracking as tracking_service

router = APIRouter(tags=["transport"])

FEATURE = tracking_service.FEATURE
gated = [Depends(require_feature(FEATURE))]
driver_only = require_roles("driver")
student_only = require_roles("student")


def reporting_driver(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                     db: Db = Depends(get_db)) -> int:
    """The driver sending a location: signed in as usual, or with the upload-only
    key of the phone's location service (POST /driver/tracking/token)."""
    if creds is None:
        raise Unauthorized("Missing token", headers={"WWW-Authenticate": "Bearer"})
    if creds.credentials.startswith(tracking_service.TOKEN_PREFIX):
        return tracking_service.driver_from_token(db, creds.credentials).id
    user = get_current_user(request, creds, db)
    if user.user_type != "driver":
        raise Forbidden("Requires role: driver")
    if not user.has(FEATURE):
        raise FeatureDisabled(FEATURE)
    return user.id


@router.post("/driver/location")
def report_location(body: LocationBatch, driver_id: int = Depends(reporting_driver), db: Db = Depends(get_db)):
    """`{interval_sec, watched, window, accepted}`: send again after `interval_sec`.
    Outside the school's tracking windows nothing is stored (`window.active` false)."""
    return tracking_service.report(db, driver_id, body)


@router.get("/driver/tracking/config", dependencies=gated)
def tracking_config(actor: CurrentUser = Depends(driver_only), db: Db = Depends(get_db)):
    return tracking_service.config(db, actor.id)


@router.post("/driver/tracking/token", dependencies=gated)
def tracking_token(actor: CurrentUser = Depends(driver_only), db: Db = Depends(get_db)):
    """`{token}`: can only call POST /driver/location; ends with this sign-in."""
    return tracking_service.issue_token(db, actor.id, actor.session_id)


@router.get("/student/transport/live", dependencies=gated)
def bus_live(user: CurrentUser = Depends(student_only), db: Db = Depends(get_db)):
    """`{state: live | stale | outside_window | no_vehicle, vehicle, position, age_sec, last_seen_min,
    moving, window, poll_sec, server_time}` for the child's own vehicle. Call every `poll_sec`
    while the Track screen is open: each call keeps the driver's phone on the fast rate."""
    return tracking_service.live(db, user.id)


@router.post("/student/transport/live/stop", dependencies=gated)
def bus_live_stop(user: CurrentUser = Depends(student_only), db: Db = Depends(get_db)):
    return tracking_service.stop_watching(db, user.id)
