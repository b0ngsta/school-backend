"""Notices for chosen classes + teachers' class announcements.

* notices.audience ('school' | 'classes') + audience_label ("Class 1, Class 2").
  A notice for some classes lists them in `notice_classes`; only students of
  those classes (and the staff of those classes) see it and get its push.
  Existing notices stay 'school' (everyone), so nothing changes for them.
* class_announcements (per school, org_id without default): a teacher's post
  (title, message, optional PDF / photo) to sections they teach;
  `class_announcement_sections` lists the sections (no org_id: reached
  through the announcement).

Deleting a class / section removes it from these lists only; the notice /
announcement stays (with its label) and never becomes school-wide, because
`audience` decides that, not an empty list.

Revision ID: 0018_class_notices
Revises: 0017_protect_records
"""
from alembic import op

from migrations.helpers import add_column

revision = "0018_class_notices"
down_revision = "0017_protect_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("notices", "audience", "VARCHAR(10) NOT NULL DEFAULT 'school'")
    add_column("notices", "audience_label", "VARCHAR(255) NULL")
    op.execute("""
        CREATE TABLE IF NOT EXISTS notice_classes (
          notice_id INT NOT NULL,
          class_id  INT NOT NULL,
          PRIMARY KEY (notice_id, class_id),
          INDEX ix_notice_classes_class (class_id),
          FOREIGN KEY (notice_id) REFERENCES notices(id) ON DELETE CASCADE,
          FOREIGN KEY (class_id)  REFERENCES classes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS class_announcements (
          id               INT          AUTO_INCREMENT PRIMARY KEY,
          org_id           INT          NOT NULL,
          created_by       INT          NULL,
          title            VARCHAR(150) NOT NULL,
          body             TEXT         NOT NULL,
          audience_label   VARCHAR(255) NULL,
          recipients_count INT          NOT NULL DEFAULT 0,
          attachment_path  VARCHAR(255) NULL,
          attachment_name  VARCHAR(255) NULL,
          attachment_type  VARCHAR(10)  NULL,
          created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_class_announcements_org_created (org_id, created_at),
          INDEX ix_class_announcements_author (created_by, created_at),
          CONSTRAINT fk_class_announcements_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS class_announcement_sections (
          announcement_id INT NOT NULL,
          section_id      INT NOT NULL,
          class_id        INT NOT NULL,
          PRIMARY KEY (announcement_id, section_id),
          INDEX ix_class_announcement_sections_section (section_id),
          FOREIGN KEY (announcement_id) REFERENCES class_announcements(id) ON DELETE CASCADE,
          FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
          FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS class_announcement_sections")
    op.execute("DROP TABLE IF EXISTS class_announcements")
    op.execute("DROP TABLE IF EXISTS notice_classes")
    op.execute("ALTER TABLE notices DROP COLUMN audience_label, DROP COLUMN audience")
