"""Substitute teachers for periods of a teacher on leave, and cancelled leaves.

* substitution_requests: a teacher going on leave asks a colleague to take
  one period on one date. MySQL/TiDB have no partial unique indexes, so the
  rules "one accepted cover per requester period", "a substitute can't take
  two periods at once" and "no duplicate open request" use virtual columns
  that are 1 for the rows the rule applies to and NULL otherwise (NULLs never
  clash in a UNIQUE index) — the same technique as users.principal_slot.
* leave_requests.status gains 'cancelled' (an approved leave cancelled
  before it started keeps its history).

Revision ID: 0007_substitutions
Revises: 0006_school_timings
"""
from alembic import op

revision = "0007_substitutions"
down_revision = "0006_school_timings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS substitution_requests (
          id                 INT          AUTO_INCREMENT PRIMARY KEY,
          leave_id           INT          NULL,
          requester_id       INT          NOT NULL,
          substitute_id      INT          NOT NULL,
          date               DATE         NOT NULL,
          period             SMALLINT     NOT NULL,
          timetable_slot_id  INT          NULL,
          class_id           INT          NOT NULL,
          section_id         INT          NOT NULL,
          subject            VARCHAR(100) NULL,
          status             ENUM('pending','accepted','declined','cancelled','withdrawn','filled')
                             NOT NULL DEFAULT 'pending',
          note               TEXT         NULL,
          response_note      TEXT         NULL,
          responded_at       TIMESTAMP    NULL,
          created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          accepted_slot      TINYINT      GENERATED ALWAYS AS (IF(status = 'accepted', 1, NULL)) VIRTUAL,
          open_slot          TINYINT      GENERATED ALWAYS AS (IF(status IN ('pending', 'accepted'), 1, NULL)) VIRTUAL,
          INDEX ix_sub_req_substitute (substitute_id, status, date),
          INDEX ix_sub_req_requester (requester_id, status, date),
          INDEX ix_sub_req_date (date, period),
          UNIQUE KEY ux_sub_req_one_cover (requester_id, date, period, accepted_slot),
          UNIQUE KEY ux_sub_req_sub_busy (substitute_id, date, period, accepted_slot),
          UNIQUE KEY ux_sub_req_no_dup (requester_id, substitute_id, date, period, open_slot),
          FOREIGN KEY (leave_id)          REFERENCES leave_requests(id)  ON DELETE SET NULL,
          FOREIGN KEY (requester_id)      REFERENCES users(id)           ON DELETE CASCADE,
          FOREIGN KEY (substitute_id)     REFERENCES users(id)           ON DELETE CASCADE,
          FOREIGN KEY (timetable_slot_id) REFERENCES timetable_slots(id) ON DELETE SET NULL,
          FOREIGN KEY (class_id)          REFERENCES classes(id)         ON DELETE CASCADE,
          FOREIGN KEY (section_id)        REFERENCES sections(id)        ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute(
        "ALTER TABLE leave_requests MODIFY COLUMN status "
        "ENUM('pending','approved','rejected','cancelled') NOT NULL DEFAULT 'pending'"
    )


def downgrade() -> None:
    op.execute("UPDATE leave_requests SET status = 'rejected' WHERE status = 'cancelled'")
    op.execute(
        "ALTER TABLE leave_requests MODIFY COLUMN status "
        "ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending'"
    )
    op.execute("DROP TABLE IF EXISTS substitution_requests")
