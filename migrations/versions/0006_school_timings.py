"""School timings: when the day starts, prayer, period length, number of
periods and breaks (a single row; every timetable shows clock times from it).

Revision ID: 0006_school_timings
Revises: 0005_notifications
"""
from alembic import op

revision = "0006_school_timings"
down_revision = "0005_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS school_timings (
          id               INT       AUTO_INCREMENT PRIMARY KEY,
          start_time       TIME      NOT NULL,
          prayer_minutes   SMALLINT  NOT NULL DEFAULT 0,
          period_minutes   SMALLINT  NOT NULL,
          periods_per_day  SMALLINT  NOT NULL DEFAULT 8,
          breaks           JSON      NOT NULL,
          updated_by       INT       NULL,
          updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS school_timings")
