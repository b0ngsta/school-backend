"""School requests of Nov 2026: simpler classes, fee instalments + undo of a
payment, two-step fee claims, exam format / grading / final result.

* classes.has_sections: 0 = a class that is not split into sections. It still
  has ONE section row (name '') so attendance, class teacher, timetable…
  work exactly like any other section; clients just don't show a section.
* class_fee_installments + fees.installment_id: "Generate class fees" now
  bills a PART of the class's fee (title, amount, due date); the parts of a
  session may add up to at most classes.fee_amount.
* transactions.status gains 'reversed': a paid fee put back to unpaid keeps
  its payment row, marked reversed (nothing is deleted).
* fee_claim_forms: validated_by / validated_at / validation_note (the office
  confirms what the class teacher checked; only then a fee is marked paid) and
  edited_by / edited_at (a reviewer corrected the parent's details). Forms
  reviewed before this migration count as validated.
* exam_format_parts: the exams that make up a class's final result for a
  session (the "exam format"), in order, each with the marks it carries in
  the final result (Notebook 5 + Handwriting 5 + Term 1 20 + Final 70 = 100).
* grade_bands: the school's own grading system (percentage range → grade).
* final_results: a row = the final result of that section and session has
  been published to parents.

Additive only.

Revision ID: 0021_nov_requests
Revises: 0020_fee_claim_forms
"""
from alembic import op

from migrations.helpers import add_column, has_column, has_table

revision = "0021_nov_requests"
down_revision = "0020_fee_claim_forms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("classes", "has_sections", "TINYINT(1) NOT NULL DEFAULT 1")

    op.execute("""
        CREATE TABLE IF NOT EXISTS class_fee_installments (
          id         INT           AUTO_INCREMENT PRIMARY KEY,
          org_id     INT           NOT NULL,
          class_id   INT           NOT NULL,
          session    VARCHAR(9)    NOT NULL,
          title      VARCHAR(100)  NOT NULL,
          amount     DECIMAL(10,2) NOT NULL,
          due_date   DATE          NULL,
          created_by INT           NULL,
          created_at TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_class_fee_installment (class_id, session, title),
          INDEX ix_class_fee_installments_org (org_id, session),
          CONSTRAINT fk_cfi_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_cfi_class FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
          CONSTRAINT fk_cfi_author FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    if not has_column("fees", "installment_id"):
        op.execute("ALTER TABLE fees ADD COLUMN installment_id INT NULL")
        op.execute("ALTER TABLE fees ADD CONSTRAINT fk_fees_installment FOREIGN KEY (installment_id) "
                   "REFERENCES class_fee_installments(id) ON DELETE SET NULL")

    op.execute("ALTER TABLE transactions MODIFY COLUMN status "
               "ENUM('success','pending','failed','reversed') NOT NULL DEFAULT 'success'")

    fresh = not has_column("fee_claim_forms", "validated_at")
    add_column("fee_claim_forms", "validated_by", "INT NULL")
    add_column("fee_claim_forms", "validated_at", "TIMESTAMP NULL")
    add_column("fee_claim_forms", "validation_note", "VARCHAR(255) NULL")
    add_column("fee_claim_forms", "edited_by", "INT NULL")
    add_column("fee_claim_forms", "edited_at", "TIMESTAMP NULL")
    if fresh:
        op.execute("ALTER TABLE fee_claim_forms ADD CONSTRAINT fk_fee_claim_forms_validator "
                   "FOREIGN KEY (validated_by) REFERENCES users(id) ON DELETE SET NULL")
        op.execute("ALTER TABLE fee_claim_forms ADD CONSTRAINT fk_fee_claim_forms_editor "
                   "FOREIGN KEY (edited_by) REFERENCES users(id) ON DELETE SET NULL")
        # what was accepted / declined under the one-step rule is final
        op.execute("UPDATE fee_claim_forms SET validated_by = reviewed_by, validated_at = reviewed_at "
                   "WHERE status IN ('accepted', 'declined') AND validated_at IS NULL")

    op.execute("""
        CREATE TABLE IF NOT EXISTS exam_format_parts (
          id           INT          AUTO_INCREMENT PRIMARY KEY,
          org_id       INT          NOT NULL,
          class_id     INT          NOT NULL,
          session      VARCHAR(9)   NOT NULL,
          exam_id      INT          NOT NULL,
          max_marks    DECIMAL(6,2) NOT NULL,
          position     INT          NOT NULL DEFAULT 0,
          created_exam TINYINT(1)   NOT NULL DEFAULT 0,
          created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_exam_format_part (class_id, exam_id),
          INDEX ix_exam_format_parts_class (org_id, class_id, session),
          CONSTRAINT fk_exam_format_parts_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_exam_format_parts_class FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
          CONSTRAINT fk_exam_format_parts_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS grade_bands (
          id          INT          AUTO_INCREMENT PRIMARY KEY,
          org_id      INT          NOT NULL,
          grade       VARCHAR(10)  NOT NULL,
          min_percent DECIMAL(5,2) NOT NULL,
          max_percent DECIMAL(5,2) NOT NULL,
          position    INT          NOT NULL DEFAULT 0,
          UNIQUE KEY uq_grade_bands_org_grade (org_id, grade),
          CONSTRAINT fk_grade_bands_org FOREIGN KEY (org_id) REFERENCES organisations(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS final_results (
          id           INT        AUTO_INCREMENT PRIMARY KEY,
          org_id       INT        NOT NULL,
          class_id     INT        NOT NULL,
          section_id   INT        NOT NULL,
          session      VARCHAR(9) NOT NULL,
          published_by INT        NULL,
          published_at TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_final_results_section (section_id, session),
          INDEX ix_final_results_org (org_id, session),
          CONSTRAINT fk_final_results_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_final_results_class FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
          CONSTRAINT fk_final_results_section FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
          CONSTRAINT fk_final_results_author FOREIGN KEY (published_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS final_results")
    op.execute("DROP TABLE IF EXISTS grade_bands")
    op.execute("DROP TABLE IF EXISTS exam_format_parts")
    if has_column("fee_claim_forms", "validated_at"):
        op.execute("ALTER TABLE fee_claim_forms DROP FOREIGN KEY fk_fee_claim_forms_validator")
        op.execute("ALTER TABLE fee_claim_forms DROP FOREIGN KEY fk_fee_claim_forms_editor")
        for column in ("edited_at", "edited_by", "validation_note", "validated_at", "validated_by"):
            op.execute(f"ALTER TABLE fee_claim_forms DROP COLUMN {column}")
    op.execute("UPDATE transactions SET status = 'failed' WHERE status = 'reversed'")
    op.execute("ALTER TABLE transactions MODIFY COLUMN status "
               "ENUM('success','pending','failed') NOT NULL DEFAULT 'success'")
    if has_column("fees", "installment_id"):
        op.execute("ALTER TABLE fees DROP FOREIGN KEY fk_fees_installment")
        op.execute("ALTER TABLE fees DROP COLUMN installment_id")
    if has_table("class_fee_installments"):
        op.execute("DROP TABLE class_fee_installments")
    if has_column("classes", "has_sections"):
        op.execute("ALTER TABLE classes DROP COLUMN has_sections")
