"""Report-card designs and saved print runs (web app "Report Cards", Nov 2026).

* print_templates: a school's own report-card designs, made in the web app's
  designer. `kind` = 'report_card' (front side) or 'report_card_back' (back
  side); `layout` = the design as JSON (page + positioned elements, in mm).
  One default per kind and school (enforced by the service).
* print_batches: report cards the office decided to print LATER — students
  skipped because some marks were neither entered nor marked absent. The web
  app lists them under "Waiting to print" and prints them once fixed.

Additive only.

Revision ID: 0025_print_templates
Revises: 0024_sibling_usernames
"""
from alembic import op

revision = "0025_print_templates"
down_revision = "0024_sibling_usernames"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS print_templates (
          id          INT          AUTO_INCREMENT PRIMARY KEY,
          org_id      INT          NOT NULL,
          kind        VARCHAR(30)  NOT NULL DEFAULT 'report_card',
          name        VARCHAR(80)  NOT NULL,
          layout      MEDIUMTEXT   NOT NULL,
          is_default  TINYINT(1)   NOT NULL DEFAULT 0,
          created_by  INT          NULL,
          updated_by  INT          NULL,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_print_templates_org_kind (org_id, kind),
          CONSTRAINT fk_print_templates_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_print_templates_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
          CONSTRAINT fk_print_templates_editor FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS print_batches (
          id                 INT           AUTO_INCREMENT PRIMARY KEY,
          org_id             INT           NOT NULL,
          source             VARCHAR(10)   NOT NULL,
          exam_id            INT           NULL,
          class_id           INT           NULL,
          front_template_id  INT           NULL,
          back_template_id   INT           NULL,
          title              VARCHAR(160)  NOT NULL,
          pending            MEDIUMTEXT    NOT NULL,
          printed_count      INT           NOT NULL DEFAULT 0,
          status             VARCHAR(10)   NOT NULL DEFAULT 'open',
          created_by         INT           NULL,
          created_at         TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at         TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX ix_print_batches_org_status (org_id, status, id),
          CONSTRAINT fk_print_batches_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_print_batches_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE SET NULL,
          CONSTRAINT fk_print_batches_class FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE SET NULL,
          CONSTRAINT fk_print_batches_front FOREIGN KEY (front_template_id) REFERENCES print_templates(id)
            ON DELETE SET NULL,
          CONSTRAINT fk_print_batches_back FOREIGN KEY (back_template_id) REFERENCES print_templates(id)
            ON DELETE SET NULL,
          CONSTRAINT fk_print_batches_creator FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS print_batches")
    op.execute("DROP TABLE IF EXISTS print_templates")
