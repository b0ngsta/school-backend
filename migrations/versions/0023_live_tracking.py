"""Live bus tracking from the driver's phone.

* vehicle_locations: ONE row per vehicle: the newest point the driver's
  phone sent, and `watched_until` (a parent has the Track screen open until
  then, so the phone reports every few seconds instead of once a minute).
* vehicle_location_history: about one point a minute per moving vehicle,
  kept 7 days (services/retention.py).
* driver_tracking_tokens: a key that can do exactly one thing, send the
  location of the driver's own vehicle (POST /driver/location). The phone's
  location service keeps it, so it can report while the app itself is closed.
  It dies with the sign-in it was made from.
* school_timings.tracking_before_minutes / tracking_after_minutes: how long
  before the school day starts and after it ends the buses are tracked
  (NULL = 120 minutes each).

All timestamps here are naive UTC (like the auth tables). Additive only.

Revision ID: 0023_live_tracking
Revises: 0022_theme_gallery
"""
from alembic import op

from migrations.helpers import add_column, has_column

revision = "0023_live_tracking"
down_revision = "0022_theme_gallery"
branch_labels = None
depends_on = None

WINDOW_COLUMNS = ("tracking_before_minutes", "tracking_after_minutes")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_locations (
          vehicle_id       INT           NOT NULL PRIMARY KEY,
          org_id           INT           NOT NULL,
          latitude         DECIMAL(9,6)  NULL,
          longitude        DECIMAL(9,6)  NULL,
          speed            FLOAT         NULL,
          heading          FLOAT         NULL,
          accuracy         FLOAT         NULL,
          recorded_at      DATETIME(3)   NULL,
          received_at      DATETIME(3)   NULL,
          history_at       DATETIME(3)   NULL,
          reported_by      INT           NULL,
          watched_until    DATETIME(3)   NULL,
          watch_started_at DATETIME(3)   NULL,
          INDEX ix_vehicle_locations_org (org_id),
          CONSTRAINT fk_vehicle_locations_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_vehicle_locations_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE,
          CONSTRAINT fk_vehicle_locations_driver FOREIGN KEY (reported_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_location_history (
          id          BIGINT        AUTO_INCREMENT PRIMARY KEY,
          org_id      INT           NOT NULL,
          vehicle_id  INT           NOT NULL,
          latitude    DECIMAL(9,6)  NOT NULL,
          longitude   DECIMAL(9,6)  NOT NULL,
          speed       FLOAT         NULL,
          heading     FLOAT         NULL,
          accuracy    FLOAT         NULL,
          recorded_at DATETIME(3)   NOT NULL,
          INDEX ix_vehicle_history_vehicle (vehicle_id, recorded_at),
          INDEX ix_vehicle_history_recorded (recorded_at),
          CONSTRAINT fk_vehicle_history_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_vehicle_history_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS driver_tracking_tokens (
          id           INT          AUTO_INCREMENT PRIMARY KEY,
          org_id       INT          NOT NULL,
          user_id      INT          NOT NULL,
          session_id   CHAR(32)     NOT NULL,
          token_hash   CHAR(64)     NOT NULL,
          created_at   DATETIME(6)  NOT NULL,
          last_used_at DATETIME(6)  NULL,
          UNIQUE KEY uq_tracking_tokens_hash (token_hash),
          UNIQUE KEY uq_tracking_tokens_session (session_id),
          INDEX ix_tracking_tokens_user (user_id),
          CONSTRAINT fk_tracking_tokens_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_tracking_tokens_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    for column in WINDOW_COLUMNS:
        add_column("school_timings", column, "SMALLINT NULL")


def downgrade() -> None:
    for column in WINDOW_COLUMNS:
        if has_column("school_timings", column):
            op.execute(f"ALTER TABLE school_timings DROP COLUMN {column}")
    op.execute("DROP TABLE IF EXISTS driver_tracking_tokens")
    op.execute("DROP TABLE IF EXISTS vehicle_location_history")
    op.execute("DROP TABLE IF EXISTS vehicle_locations")
