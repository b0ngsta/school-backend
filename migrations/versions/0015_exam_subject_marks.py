"""Exam marks per subject, with a maximum.

Before this, a student had one mark per exam (no subjects) and any number
was accepted, negative ones included.

* exams.max_marks: the usual maximum for each subject paper (e.g. 100);
  optional.
* exam_results.subject: '' = the whole exam (every row written before this,
  and apps that don't send a subject); otherwise the subject's name, as in
  class_subjects / exam_papers. One row per exam + student + subject.
* exam_results.max_marks: the maximum the mark was entered against, so a
  later change of the exam's default doesn't rewrite old marks.

Revision ID: 0015_exam_subject_marks
Revises: 0014_login_captcha
"""
from alembic import op

from migrations.helpers import add_column, add_index, drop_index, has_column

revision = "0015_exam_subject_marks"
down_revision = "0014_login_captcha"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("exams", "max_marks", "DECIMAL(6,2) NULL")
    add_column("exam_results", "subject", "VARCHAR(100) NOT NULL DEFAULT ''")
    add_column("exam_results", "max_marks", "DECIMAL(6,2) NULL")
    # the new key first: the old one also serves the foreign key on exam_id
    add_index("exam_results", "uq_exam_student_subject", "exam_id, student_id, subject", unique=True)
    drop_index("exam_results", "uq_exam_student")


def downgrade() -> None:
    op.execute("DELETE FROM exam_results WHERE subject <> ''")
    add_index("exam_results", "uq_exam_student", "exam_id, student_id", unique=True)
    drop_index("exam_results", "uq_exam_student_subject")
    for table, column in (("exam_results", "max_marks"), ("exam_results", "subject"), ("exams", "max_marks")):
        if has_column(table, column):
            op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
