"""Bus tracking per driver, following the driver's shifts (Nov 2026).

* driver_tracking_settings: per driver account, how many minutes before each
  of their shifts the phone starts sharing its location (`before_minutes`) and
  how long after the shift it stops (`after_minutes`). NULL = the school's
  default (school_timings.tracking_before_minutes / tracking_after_minutes).
  A driver without shifts (transport_shifts) keeps the school's two windows.

Additive only.

Revision ID: 0027_driver_tracking
Revises: 0026_sibling_devices
"""
from alembic import op

revision = "0027_driver_tracking"
down_revision = "0026_sibling_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS driver_tracking_settings (
          driver_id       INT       NOT NULL PRIMARY KEY,
          org_id          INT       NOT NULL,
          before_minutes  SMALLINT  NULL,
          after_minutes   SMALLINT  NULL,
          updated_by      INT       NULL,
          updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_driver_tracking_org (org_id),
          CONSTRAINT fk_driver_tracking_driver FOREIGN KEY (driver_id) REFERENCES users(id) ON DELETE CASCADE,
          CONSTRAINT fk_driver_tracking_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_driver_tracking_by FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS driver_tracking_settings")
