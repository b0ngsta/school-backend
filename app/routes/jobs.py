"""Scheduled jobs, triggered over HTTP by an external cron (Render cron job,
crontab + curl, GitHub Actions …) with the secret from JOBS_SECRET:

    curl -X POST -H "X-Jobs-Secret: $JOBS_SECRET" https://api.example.com/internal/jobs/fee-reminders

    /internal/jobs/fee-reminders   due-soon and overdue fee reminders (daily, ~08:00)
    /internal/jobs/run             run due background jobs (only needed with JOBS_WORKER=false)
    /internal/jobs/cleanup         delete old notifications, audit entries… (the API already does
                                   this once a day unless RETENTION_ENABLED=false)

Without JOBS_SECRET the endpoints don't exist (404).
"""
import hmac

from fastapi import APIRouter, Depends, Header

from app.core.config import settings
from app.core.errors import Forbidden, NotFound
from app.db import Db
from app.dependencies import get_db
from app.services import background, reminders, retention

router = APIRouter(prefix="/internal/jobs", tags=["jobs"], include_in_schema=False)


def _check_secret(x_jobs_secret: str | None = Header(default=None)) -> None:
    if not settings.jobs_secret:
        raise NotFound("Not Found")
    if not x_jobs_secret or not hmac.compare_digest(x_jobs_secret.encode(), settings.jobs_secret.encode()):
        raise Forbidden("Invalid job secret")


@router.post("/fee-reminders", dependencies=[Depends(_check_secret)])
def fee_reminders(db: Db = Depends(get_db)):
    """Due-soon (3 days) and overdue fee reminders; safe to run more than once a day."""
    return reminders.send_fee_reminders(db)


@router.post("/run", dependencies=[Depends(_check_secret)])
def run_jobs():
    """Run due background jobs now (for hosts where the API's worker thread is off: JOBS_WORKER=false)."""
    return {"ran": background.run_pending(limit=200)}


@router.post("/cleanup", dependencies=[Depends(_check_secret)])
def cleanup(db: Db = Depends(get_db)):
    """The daily clean-up of old notifications, audit entries, sessions… (services/retention.py)."""
    return retention.run(db)
