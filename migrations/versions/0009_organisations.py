"""Organisations: one API, many schools.

* organisations: one row per school (code typed at login, display name,
  active flag). The school that existed before this migration becomes
  organisation 1 (code DEFAULT_ORG_CODE, default "DEFAULT"; name SCHOOL_NAME).
* Every table whose rows belong to one school gets `org_id` (all existing
  rows -> organisation 1). The column has NO default, so an INSERT that
  forgets it fails instead of silently landing in another school.
* Unique rules become per school: usernames, class names, calendar event
  titles, holiday dates, vehicle numbers, admission numbers, "only one
  principal" and the single school-timings row.

Child tables reached only through a parent (sections' subjects, exam
results, lesson files, sessions, notifications, …) stay as they are.

Revision ID: 0009_organisations
Revises: 0008_class_homework
"""
import os
import re

from alembic import op
from sqlalchemy import text

from migrations.helpers import add_index, drop_index, has_column, has_index, has_table

revision = "0009_organisations"
down_revision = "0008_class_homework"
branch_labels = None
depends_on = None

DEFAULT_ORG_ID = 1

# tables that get org_id (audit_logs allows NULL: some entries have no school)
ORG_TABLES = (
    "users", "classes", "sections", "teacher_assignments", "student_profiles", "attendance",
    "fees", "fee_claims", "transactions", "lesson_plans", "notices", "enquiries", "exams",
    "timetable_slots", "calendar_events", "holidays", "leave_requests", "salary_records",
    "staff_attendance", "vehicles", "sms_messages", "school_timings", "substitution_requests",
    "class_homework", "audit_logs",
)
NULLABLE = {"audit_logs"}

# (table, old unique index, its columns, new per-school unique index, new columns)
UNIQUES = (
    ("users", "username", "username", "uq_users_org_username", "org_id, username"),
    ("users", "uq_users_single_principal", "principal_slot", "uq_users_org_principal", "org_id, principal_slot"),
    ("classes", "name", "name", "uq_classes_org_name", "org_id, name"),
    ("calendar_events", "title", "title", "uq_calendar_events_org_title", "org_id, title"),
    ("holidays", "date", "date", "uq_holidays_org_date", "org_id, date"),
    ("vehicles", "vehicle_no", "vehicle_no", "uq_vehicles_org_vehicle_no", "org_id, vehicle_no"),
    ("student_profiles", "uq_student_admission_no", "admission_no", "uq_student_org_admission_no",
     "org_id, admission_no"),
)


def _default_code() -> str:
    code = re.sub(r"[^A-Z0-9-]", "", (os.environ.get("DEFAULT_ORG_CODE") or "DEFAULT").strip().upper())
    return code[:30] if len(code) >= 2 else "DEFAULT"


def _default_name() -> str:
    return ((os.environ.get("SCHOOL_NAME") or "").strip() or "My School")[:150]


def _single_column_unique(table: str, column: str) -> str | None:
    """Name of a UNIQUE index made of exactly `column` (legacy databases may
    have named it differently)."""
    rows = op.get_bind().execute(text(
        "SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols "
        "FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = :t "
        "AND non_unique = 0 GROUP BY index_name"
    ), {"t": table})
    for row in rows:
        if row.cols == column and row.index_name != "PRIMARY":
            return row.index_name
    return None


def _has_fk(table: str, name: str) -> bool:
    return bool(op.get_bind().execute(text(
        "SELECT COUNT(*) FROM information_schema.table_constraints WHERE table_schema = DATABASE() "
        "AND table_name = :t AND constraint_name = :n AND constraint_type = 'FOREIGN KEY'"
    ), {"t": table, "n": name}).scalar())


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS organisations (
          id          INT          AUTO_INCREMENT PRIMARY KEY,
          code        VARCHAR(30)  NOT NULL,
          name        VARCHAR(150) NOT NULL,
          is_active   TINYINT(1)   NOT NULL DEFAULT 1,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_organisations_code (code)
        ) ENGINE=InnoDB
    """)
    bind = op.get_bind()
    if not bind.execute(text("SELECT COUNT(*) FROM organisations WHERE id = :i"), {"i": DEFAULT_ORG_ID}).scalar():
        bind.execute(text("INSERT INTO organisations (id, code, name) VALUES (:i, :c, :n)"),
                     {"i": DEFAULT_ORG_ID, "c": _default_code(), "n": _default_name()})

    for table in ORG_TABLES:
        if not has_table(table) or has_column(table, "org_id"):
            continue
        if table in NULLABLE:
            op.execute(f"ALTER TABLE {table} ADD COLUMN org_id INT NULL")
            op.execute(f"UPDATE {table} SET org_id = {DEFAULT_ORG_ID}")
        else:
            # the default fills existing rows; dropping it makes every new row name its school
            op.execute(f"ALTER TABLE {table} ADD COLUMN org_id INT NOT NULL DEFAULT {DEFAULT_ORG_ID}")
            op.execute(f"ALTER TABLE {table} ALTER COLUMN org_id DROP DEFAULT")
        fk = f"fk_{table}_org"
        if not _has_fk(table, fk):
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {fk} FOREIGN KEY (org_id) REFERENCES organisations(id)")

    for table, old, column, new, columns in UNIQUES:
        add_index(table, new, columns, unique=True)  # new rule first: uniqueness holds throughout
        existing = old if has_index(table, old) else _single_column_unique(table, column)
        if existing:
            drop_index(table, existing)
    add_index("school_timings", "uq_school_timings_org", "org_id", unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(text("SELECT COUNT(*) FROM organisations")).scalar() if has_table("organisations") else 0
    if count and count > 1:
        raise RuntimeError(
            "This database holds more than one organisation; going back to a single-school schema would "
            "mix their data. Export and remove the extra organisations first."
        )
    # foreign keys first: MySQL may be using a per-school unique index (org_id, …) to enforce them
    tables = [t for t in reversed(ORG_TABLES) if has_table(t) and has_column(t, "org_id")]
    for table in tables:
        fk = f"fk_{table}_org"
        if _has_fk(table, fk):
            op.execute(f"ALTER TABLE {table} DROP FOREIGN KEY {fk}")
        drop_index(table, fk)
    drop_index("school_timings", "uq_school_timings_org")
    for table, old, column, new, _ in reversed(UNIQUES):
        add_index(table, old, column, unique=True)
        drop_index(table, new)
    for table in tables:
        op.execute(f"ALTER TABLE {table} DROP COLUMN org_id")
    op.execute("DROP TABLE IF EXISTS organisations")
