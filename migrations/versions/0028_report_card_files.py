"""Report-card PDFs released to parents (Nov 2026).

* report_card_files: one stored PDF per student per result. The office's
  browser prints the whole class to ONE PDF ("Save as PDF") and uploads it;
  the server cuts it into one file per student (pikepdf) and stores each in
  the normal upload storage. `result_key` = "exam:<exam id>" or
  "final:<session>" — releasing the same result again replaces the file.
  Nothing is rendered on the server.

Additive only.

Revision ID: 0028_report_card_files
Revises: 0027_driver_tracking
"""
from alembic import op

revision = "0028_report_card_files"
down_revision = "0027_driver_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS report_card_files (
          id                INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
          org_id            INT          NOT NULL,
          student_id        INT          NOT NULL,
          result_key        VARCHAR(40)  NOT NULL,
          source            ENUM('exam','final') NOT NULL,
          exam_id           INT          NULL,
          session           VARCHAR(9)   NULL,
          class_id          INT          NULL,
          section_id        INT          NULL,
          title             VARCHAR(160) NOT NULL,
          file_path         VARCHAR(255) NOT NULL,
          file_size         INT          NOT NULL DEFAULT 0,
          pages             SMALLINT     NOT NULL DEFAULT 1,
          front_template_id INT          NULL,
          back_template_id  INT          NULL,
          released_by       INT          NULL,
          released_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_report_card_files_student_result (org_id, student_id, result_key),
          INDEX ix_report_card_files_org_result (org_id, result_key),
          INDEX ix_report_card_files_student (student_id, released_at),
          CONSTRAINT fk_rcf_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_rcf_student FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE RESTRICT,
          CONSTRAINT fk_rcf_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE SET NULL,
          CONSTRAINT fk_rcf_class FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE SET NULL,
          CONSTRAINT fk_rcf_section FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL,
          CONSTRAINT fk_rcf_front FOREIGN KEY (front_template_id) REFERENCES print_templates(id) ON DELETE SET NULL,
          CONSTRAINT fk_rcf_back FOREIGN KEY (back_template_id) REFERENCES print_templates(id) ON DELETE SET NULL,
          CONSTRAINT fk_rcf_by FOREIGN KEY (released_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS report_card_files")
