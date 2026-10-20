"""Homework for a whole class-section, with a per-student submission status.

A missing homework_submissions row means 'pending', so students who join
the section later see the homework without any back-filling.

Revision ID: 0008_class_homework
Revises: 0007_substitutions
"""
from alembic import op

revision = "0008_class_homework"
down_revision = "0007_substitutions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS class_homework (
          id          INT          AUTO_INCREMENT PRIMARY KEY,
          class_id    INT          NOT NULL,
          section_id  INT          NOT NULL,
          subject     VARCHAR(100) NULL,
          title       VARCHAR(200) NOT NULL,
          note        TEXT         NULL,
          due_date    DATE         NULL,
          created_by  INT          NOT NULL,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_class_homework_section (class_id, section_id, created_at),
          INDEX ix_class_homework_section_only (section_id, created_at),
          FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE,
          FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
          FOREIGN KEY (created_by) REFERENCES users(id)    ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS homework_submissions (
          homework_id INT       NOT NULL,
          student_id  INT       NOT NULL,
          status      ENUM('pending','submitted','late') NOT NULL DEFAULT 'pending',
          updated_by  INT       NULL,
          updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (homework_id, student_id),
          INDEX ix_homework_submissions_student (student_id),
          FOREIGN KEY (homework_id) REFERENCES class_homework(id) ON DELETE CASCADE,
          FOREIGN KEY (student_id)  REFERENCES users(id)          ON DELETE CASCADE,
          FOREIGN KEY (updated_by)  REFERENCES users(id)          ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS homework_submissions")
    op.execute("DROP TABLE IF EXISTS class_homework")
