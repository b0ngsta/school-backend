"""Driver "My Routine": several shifts per vehicle, each with its own ordered
list of students (stop, time, landmark, map pin, notes).

Before this a vehicle had exactly one shift (vehicles.shift_*) and every
student one seat (student_transport). Both stay as they are: a vehicle with
no rows here is still shown to its driver as that one shift.

* transport_shifts: one trip of a vehicle — name ("Morning Shift 1"),
  pickup (home → school) or drop (school → home), start / end time and the
  weekdays it runs (Mon=0 … Sat=5, Sunday=6). Per school (org_id).
* transport_shift_students: the students of a shift in stop order, with the
  stop's time and where to stop. Child table reached only through a
  validated shift, so no org_id (same rule as message_recipients).

Additive only: two new tables, nothing existing changes.

Revision ID: 0012_driver_routine
Revises: 0011_student_portal
"""
from alembic import op

revision = "0012_driver_routine"
down_revision = "0011_student_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS transport_shifts (
          id          INT          AUTO_INCREMENT PRIMARY KEY,
          org_id      INT          NOT NULL,
          vehicle_id  INT          NOT NULL,
          name        VARCHAR(50)  NOT NULL,
          trip_type   ENUM('pickup','drop') NOT NULL,
          start_time  TIME         NOT NULL,
          end_time    TIME         NOT NULL,
          weekdays    VARCHAR(20)  NOT NULL DEFAULT '0,1,2,3,4,5',
          is_active   TINYINT(1)   NOT NULL DEFAULT 1,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_transport_shifts_vehicle (org_id, vehicle_id, start_time),
          CONSTRAINT fk_transport_shifts_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS transport_shift_students (
          shift_id    INT          NOT NULL,
          student_id  INT          NOT NULL,
          stop_order  SMALLINT     NOT NULL DEFAULT 0,
          stop_time   TIME         NULL,
          stop_name   VARCHAR(150) NULL,
          landmark    VARCHAR(150) NULL,
          latitude    DECIMAL(9,6) NULL,
          longitude   DECIMAL(9,6) NULL,
          notes       VARCHAR(300) NULL,
          PRIMARY KEY (shift_id, student_id),
          INDEX ix_transport_shift_students_student (student_id),
          FOREIGN KEY (shift_id)   REFERENCES transport_shifts(id) ON DELETE CASCADE,
          FOREIGN KEY (student_id) REFERENCES users(id)            ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS transport_shift_students")
    op.execute("DROP TABLE IF EXISTS transport_shifts")
