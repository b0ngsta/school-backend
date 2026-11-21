"""Driver details (Nov 2026): the driving licence number, filled by the bulk
school import (drivers.xlsx "Licence no.").

* driver_profiles: one row per driver account (only drivers who have details).

Additive only.

Revision ID: 0029_driver_profiles
Revises: 0028_report_card_files
"""
from alembic import op

revision = "0029_driver_profiles"
down_revision = "0028_report_card_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS driver_profiles (
          user_id     INT          NOT NULL PRIMARY KEY,
          licence_no  VARCHAR(30)  NULL,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          CONSTRAINT fk_driver_profiles_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS driver_profiles")
