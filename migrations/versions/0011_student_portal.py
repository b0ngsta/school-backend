"""Student portal: school profile, launch announcements, documents
(timetables / syllabus), attachments, messages, events, transport
assignments, half-day attendance, teacher designation.

Everything is additive: new tables, new nullable columns and one widened
ENUM, so apps and web builds released before this migration keep working.

* organisations: school profile shown in the app (logo, director, principal,
  affiliation number, contacts, address) + `session_start_month` (academic
  session start, default April).
* attendance.status gains 'half_day'.
* teacher_profiles.designation (e.g. "TGT").
* notices / class_homework: optional attachment (PDF or image).
* vehicles: type (bus / van / other), linked driver account, shift.
* New per-school tables (all with org_id, no default): announcements,
  school_documents, messages, events, student_transport. Their child tables
  (announcement_images, message_recipients, event_media) are reached through
  the validated parent and carry no org_id.

Revision ID: 0011_student_portal
Revises: 0010_platform_indexes
"""
from alembic import op

from migrations.helpers import add_column, add_index, drop_index, has_column, has_table

revision = "0011_student_portal"
down_revision = "0010_platform_indexes"
branch_labels = None
depends_on = None

ORG_PROFILE_COLUMNS = (
    ("logo_path", "VARCHAR(255) NULL"),
    ("director_name", "VARCHAR(100) NULL"),
    ("principal_name", "VARCHAR(100) NULL"),
    ("affiliation_no", "VARCHAR(50) NULL"),
    ("phone", "VARCHAR(20) NULL"),
    ("tel", "VARCHAR(20) NULL"),
    ("email", "VARCHAR(120) NULL"),
    ("website", "VARCHAR(200) NULL"),
    ("address", "VARCHAR(300) NULL"),
    ("session_start_month", "TINYINT NOT NULL DEFAULT 4"),
)

ATTACHMENT_COLUMNS = (
    ("attachment_path", "VARCHAR(255) NULL"),
    ("attachment_name", "VARCHAR(255) NULL"),
    ("attachment_type", "VARCHAR(10) NULL"),  # 'pdf' | 'image'
)

VEHICLE_COLUMNS = (
    ("vehicle_type", "ENUM('bus','van','other') NOT NULL DEFAULT 'bus'"),
    ("driver_id", "INT NULL"),
    ("shift_name", "VARCHAR(50) NULL"),
    ("shift_start", "TIME NULL"),
    ("shift_end", "TIME NULL"),
)

NEW_TABLES = ("student_transport", "event_media", "events", "message_recipients", "messages",
              "school_documents", "announcement_images", "announcements")


def upgrade() -> None:
    for column, definition in ORG_PROFILE_COLUMNS:
        add_column("organisations", column, definition)

    op.execute("ALTER TABLE attendance MODIFY status ENUM('present','absent','leave','half_day') NOT NULL")
    add_column("teacher_profiles", "designation", "VARCHAR(50) NULL")

    for table in ("notices", "class_homework"):
        for column, definition in ATTACHMENT_COLUMNS:
            add_column(table, column, definition)

    for column, definition in VEHICLE_COLUMNS:
        add_column("vehicles", column, definition)
    _add_fk("vehicles", "fk_vehicles_driver", "driver_id", "users(id) ON DELETE SET NULL")

    op.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
          id          INT          AUTO_INCREMENT PRIMARY KEY,
          org_id      INT          NOT NULL,
          title       VARCHAR(150) NOT NULL,
          body        TEXT         NULL,
          starts_on   DATE         NULL,
          ends_on     DATE         NULL,
          is_active   TINYINT(1)   NOT NULL DEFAULT 1,
          created_by  INT          NULL,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_announcements_org_active (org_id, is_active, starts_on),
          CONSTRAINT fk_announcements_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS announcement_images (
          id              INT          AUTO_INCREMENT PRIMARY KEY,
          announcement_id INT          NOT NULL,
          image_path      VARCHAR(255) NOT NULL,
          sort            SMALLINT     NOT NULL DEFAULT 0,
          created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX ix_announcement_images (announcement_id, sort),
          FOREIGN KEY (announcement_id) REFERENCES announcements(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS school_documents (
          id              INT          AUTO_INCREMENT PRIMARY KEY,
          org_id          INT          NOT NULL,
          kind            ENUM('timetable','syllabus') NOT NULL,
          title           VARCHAR(150) NOT NULL,
          note            TEXT         NULL,
          class_id        INT          NULL,
          section_id      INT          NULL,
          subject         VARCHAR(100) NULL,
          attachment_path VARCHAR(255) NOT NULL,
          attachment_name VARCHAR(255) NULL,
          attachment_type VARCHAR(10)  NOT NULL,
          created_by      INT          NULL,
          created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_school_documents_kind (org_id, kind, class_id, created_at),
          CONSTRAINT fk_school_documents_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE,
          FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
          FOREIGN KEY (created_by) REFERENCES users(id)    ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS messages (
          id               INT          AUTO_INCREMENT PRIMARY KEY,
          org_id           INT          NOT NULL,
          sender_id        INT          NULL,
          title            VARCHAR(150) NOT NULL,
          body             TEXT         NOT NULL,
          audience         ENUM('students','section','class','school') NOT NULL,
          audience_label   VARCHAR(150) NOT NULL,
          class_id         INT          NULL,
          section_id       INT          NULL,
          recipients_count INT          NOT NULL DEFAULT 0,
          attachment_path  VARCHAR(255) NULL,
          attachment_name  VARCHAR(255) NULL,
          attachment_type  VARCHAR(10)  NULL,
          created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX ix_messages_org_created (org_id, created_at),
          INDEX ix_messages_sender (sender_id, created_at),
          CONSTRAINT fk_messages_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          FOREIGN KEY (sender_id)  REFERENCES users(id)    ON DELETE SET NULL,
          FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE SET NULL,
          FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS message_recipients (
          message_id INT         NOT NULL,
          student_id INT         NOT NULL,
          read_at    DATETIME    NULL,
          PRIMARY KEY (message_id, student_id),
          INDEX ix_message_recipients_student (student_id, message_id),
          FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
          FOREIGN KEY (student_id) REFERENCES users(id)    ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS events (
          id          INT          AUTO_INCREMENT PRIMARY KEY,
          org_id      INT          NOT NULL,
          title       VARCHAR(150) NOT NULL,
          description TEXT         NULL,
          venue       VARCHAR(150) NULL,
          start_date  DATE         NOT NULL,
          end_date    DATE         NOT NULL,
          start_time  TIME         NULL,
          end_time    TIME         NULL,
          created_by  INT          NULL,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_events_org_dates (org_id, end_date, start_date),
          CONSTRAINT fk_events_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS event_media (
          id         INT          AUTO_INCREMENT PRIMARY KEY,
          event_id   INT          NOT NULL,
          kind       ENUM('image','video') NOT NULL,
          media_path VARCHAR(255) NULL,
          url        VARCHAR(500) NULL,
          caption    VARCHAR(200) NULL,
          created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX ix_event_media_event (event_id, id),
          FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS student_transport (
          student_id  INT          PRIMARY KEY,
          org_id      INT          NOT NULL,
          vehicle_id  INT          NOT NULL,
          stop_name   VARCHAR(150) NULL,
          pickup_time TIME         NULL,
          drop_time   TIME         NULL,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_student_transport_vehicle (vehicle_id),
          CONSTRAINT fk_student_transport_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          FOREIGN KEY (student_id) REFERENCES users(id)    ON DELETE CASCADE,
          FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    add_index("class_homework", "ix_class_homework_subject", "section_id, subject, created_at")


def downgrade() -> None:
    drop_index("class_homework", "ix_class_homework_subject")
    for table in NEW_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")

    if _fk_exists("vehicles", "fk_vehicles_driver"):
        op.execute("ALTER TABLE vehicles DROP FOREIGN KEY fk_vehicles_driver")
    drop_index("vehicles", "fk_vehicles_driver")
    for column, _ in reversed(VEHICLE_COLUMNS):
        _drop_column("vehicles", column)
    for table in ("class_homework", "notices"):
        for column, _ in reversed(ATTACHMENT_COLUMNS):
            _drop_column(table, column)

    _drop_column("teacher_profiles", "designation")
    # the old ENUM has no half day: count those days as present
    op.execute("UPDATE attendance SET status = 'present' WHERE status = 'half_day'")
    op.execute("ALTER TABLE attendance MODIFY status ENUM('present','absent','leave') NOT NULL")
    for column, _ in reversed(ORG_PROFILE_COLUMNS):
        _drop_column("organisations", column)


# ---------------------------------------------------------------- helpers

def _drop_column(table: str, column: str) -> None:
    if has_table(table) and has_column(table, column):
        op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _fk_exists(table: str, name: str) -> bool:
    from sqlalchemy import text

    return bool(op.get_bind().execute(text(
        "SELECT COUNT(*) FROM information_schema.table_constraints WHERE table_schema = DATABASE() "
        "AND table_name = :t AND constraint_name = :n AND constraint_type = 'FOREIGN KEY'"
    ), {"t": table, "n": name}).scalar())


def _add_fk(table: str, name: str, column: str, target: str) -> None:
    if not _fk_exists(table, name):
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({column}) REFERENCES {target}")
