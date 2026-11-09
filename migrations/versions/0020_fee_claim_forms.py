"""Fee claim forms: a parent fills a form in the app (what the payment was for,
amount, date, method, reference, note) and attaches ONE file (PDF or photo).
The class teacher of the child's section and the managers accept or decline it.

A separate table on purpose: the older per-fee payment proofs (`fee_claims`,
POST /fees/{id}/claims, reviewed in "Fee Claims") are left exactly as they are.

* fee_claim_forms (per school, org_id without default). `fee_id` is optional:
  a form may point at one of the child's unpaid fees (accepting it can then
  mark that fee paid, like the old proofs) or just say what it was for
  (`purpose`). `class_id` / `section_id` are where the child studied when the
  form was sent: that section's class teacher reviews it.
* Deleting a fee / class / section keeps the form (SET NULL; `purpose` keeps
  the fee's title). The parent account can't be erased while it has forms
  (RESTRICT, like every owner since 0013); reviewers lose only their name.

Additive only.

Revision ID: 0020_fee_claim_forms
Revises: 0019_features
"""
from alembic import op

revision = "0020_fee_claim_forms"
down_revision = "0019_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fee_claim_forms (
          id                    INT           AUTO_INCREMENT PRIMARY KEY,
          org_id                INT           NOT NULL,
          student_id            INT           NOT NULL,
          class_id              INT           NULL,
          section_id            INT           NULL,
          fee_id                INT           NULL,
          purpose               VARCHAR(150)  NOT NULL,
          amount                DECIMAL(10,2) NOT NULL,
          paid_on               DATE          NOT NULL,
          method                VARCHAR(20)   NOT NULL,
          reference_no          VARCHAR(100)  NULL,
          note                  VARCHAR(500)  NULL,
          attachment_path       VARCHAR(255)  NOT NULL,
          attachment_name       VARCHAR(255)  NULL,
          attachment_type       VARCHAR(10)   NOT NULL,
          status                VARCHAR(10)   NOT NULL DEFAULT 'pending',
          reviewed_by           INT           NULL,
          reviewed_at           TIMESTAMP     NULL,
          review_note           VARCHAR(255)  NULL,
          fee_marked_paid       TINYINT(1)    NOT NULL DEFAULT 0,
          transaction_reference VARCHAR(40)   NULL,
          created_at            TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at            TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_fee_claim_forms_org_status (org_id, status, created_at),
          INDEX ix_fee_claim_forms_section (section_id, status, created_at),
          INDEX ix_fee_claim_forms_student (student_id, created_at),
          INDEX ix_fee_claim_forms_fee (fee_id, status),
          CONSTRAINT fk_fee_claim_forms_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_fee_claim_forms_student FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE RESTRICT,
          CONSTRAINT fk_fee_claim_forms_class FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE SET NULL,
          CONSTRAINT fk_fee_claim_forms_section FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL,
          CONSTRAINT fk_fee_claim_forms_fee FOREIGN KEY (fee_id) REFERENCES fees(id) ON DELETE SET NULL,
          CONSTRAINT fk_fee_claim_forms_reviewer FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fee_claim_forms")
