"""Platform operator API: whoever runs this API for many schools. The owner
portal (`work/dashboard`, Next.js) calls these server-side; browsers never
see the secret.

Every endpoint needs `X-Platform-Secret: $PLATFORM_SECRET`; without
PLATFORM_SECRET they do not exist (404). `X-Platform-Actor` (optional) names
the operator for the audit log.

    GET   /platform/overview                                   totals, 14-day trends, every school's row
    GET   /platform/organisations                              every school: numbers + alerts
    POST  /platform/organisations                              new school + its first admin
    GET   /platform/organisations/{code}                       one school in depth
    PATCH /platform/organisations/{code}                       rename, change code, disable / enable
    GET   /platform/organisations/{code}/users                 accounts (?role=&q=&active=&limit=&offset=)
    POST  /platform/organisations/{code}/users/{id}/password   set a new password (ends the user's sessions)
    POST  /platform/organisations/{code}/users/{id}/sign-out   end every session of the user
    GET   /platform/activity                                   audit trail (?org=&action=&before_id=&limit=)
    GET   /platform/system                                     database + configuration health
"""
from fastapi import APIRouter, Depends, Query, Response

from app.db import Db
from app.dependencies import get_db, platform_operator
from app.models.common import UserType
from app.models.organisations import OrganisationCreate, OrganisationUpdate
from app.models.platform import PlatformPasswordReset
from app.services import organisations, platform

router = APIRouter(prefix="/platform", tags=["platform"], dependencies=[Depends(platform_operator)])


@router.get("/overview")
def overview(db: Db = Depends(get_db)):
    """Totals across every school, 14-day trends (attendance, payments,
    sign-ins) and every school's row (same shape as GET /platform/organisations)."""
    return platform.overview(db)


@router.get("/organisations")
def list_schools(db: Db = Depends(get_db)):
    """Every school with user counts, today's attendance, fees, waiting
    approvals, active users and `alerts` (codes such as attendance_not_marked)."""
    return platform.school_rows(db)


@router.post("/organisations", status_code=201)
def create_school(body: OrganisationCreate, db: Db = Depends(get_db),
                  operator: str | None = Depends(platform_operator)):
    """A new school and its first admin account (must change the password at first login)."""
    return organisations.create(db, body, operator)


@router.get("/organisations/{code}")
def school_detail(code: str, db: Db = Depends(get_db)):
    return platform.school_detail(db, code)


@router.patch("/organisations/{code}")
def update_school(code: str, body: OrganisationUpdate, db: Db = Depends(get_db),
                  operator: str | None = Depends(platform_operator)):
    return organisations.update(db, code, body, operator)


@router.get("/organisations/{code}/users")
def school_users(
    code: str,
    response: Response,
    role: UserType | None = None,
    q: str | None = Query(None, max_length=100, description="Username, name, email, phone or admission no."),
    active: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Db = Depends(get_db),
):
    rows, total = platform.school_users(db, code, role=role, query=q, active=active, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.post("/organisations/{code}/users/{user_id}/password")
def set_user_password(code: str, user_id: int, body: PlatformPasswordReset, db: Db = Depends(get_db),
                      operator: str | None = Depends(platform_operator)):
    return platform.set_user_password(db, code, user_id, body, operator)


@router.post("/organisations/{code}/users/{user_id}/sign-out")
def sign_out_user(code: str, user_id: int, db: Db = Depends(get_db),
                  operator: str | None = Depends(platform_operator)):
    return platform.sign_out_user(db, code, user_id, operator)


@router.get("/activity")
def activity(
    org: str | None = Query(None, max_length=30, description="School code"),
    action: str | None = Query(None, max_length=60, description="Action prefix, e.g. 'fee' or 'auth.'"),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=platform.ACTIVITY_MAX_LIMIT),
    db: Db = Depends(get_db),
):
    return platform.activity(db, code=org, action=action, before_id=before_id, limit=limit)


@router.get("/system")
def system(db: Db = Depends(get_db)):
    return platform.system_status(db)
