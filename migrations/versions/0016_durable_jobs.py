"""Background work that survives restarts, and old rows cleaned up.

Before this, push messages, emails and password-reset lookups ran on a small
thread pool inside the API process: a deploy, a crash or Render's free-tier
sleep lost whatever was still queued.

* background_jobs: one row per job (services/background.py), written in the
  same transaction as the change that caused it, run by a worker thread in
  every API process and retried with backoff. The payload is encrypted with a
  key derived from SECRET_KEY (emails can carry reset codes).
* scheduled_runs: when a daily task (the clean-up, services/retention.py) last
  ran, so that only one process runs it per day.
* notifications(created_at): lets the clean-up delete old notifications
  without scanning the table.

Revision ID: 0016_durable_jobs
Revises: 0015_exam_subject_marks
"""
from alembic import op

from migrations.helpers import add_index, drop_index

revision = "0016_durable_jobs"
down_revision = "0015_exam_subject_marks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS background_jobs (
          id            BIGINT       AUTO_INCREMENT PRIMARY KEY,
          kind          VARCHAR(120) NOT NULL,
          payload       MEDIUMTEXT   NULL,
          status        VARCHAR(10)  NOT NULL DEFAULT 'pending',
          attempts      INT          NOT NULL DEFAULT 0,
          max_attempts  INT          NOT NULL DEFAULT 5,
          run_after     DATETIME(6)  NOT NULL,
          locked_by     VARCHAR(64)  NULL,
          locked_at     DATETIME(6)  NULL,
          last_error    VARCHAR(500) NULL,
          created_at    DATETIME(6)  NOT NULL,
          finished_at   DATETIME(6)  NULL,
          INDEX idx_jobs_due (status, run_after),
          INDEX idx_jobs_finished (finished_at)
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_runs (
          name         VARCHAR(60) NOT NULL PRIMARY KEY,
          last_run_at  DATETIME(6) NOT NULL
        ) ENGINE=InnoDB
    """)
    add_index("notifications", "ix_notifications_created", "created_at")


def downgrade() -> None:
    drop_index("notifications", "ix_notifications_created")
    op.execute("DROP TABLE IF EXISTS scheduled_runs")
    op.execute("DROP TABLE IF EXISTS background_jobs")
