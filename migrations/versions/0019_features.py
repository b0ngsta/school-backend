"""Per-school feature flags (EduManage HQ switches modules on / off per school).

* organisation_features: ONLY the overrides set from HQ (no row = the
  catalogue default in app/core/features.py, which is ON for every feature),
  so every existing school keeps working exactly as before.
* organisations.features_version: +1 with every change, so the API's
  in-memory cache of a school's flags is refreshed on the next request.

Additive only; turning a feature off never deletes data.

Revision ID: 0019_features
Revises: 0018_class_notices
"""
from alembic import op

from migrations.helpers import add_column

revision = "0019_features"
down_revision = "0018_class_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS organisation_features (
          org_id      INT          NOT NULL,
          feature_key VARCHAR(60)  NOT NULL,
          enabled     TINYINT(1)   NOT NULL,
          note        VARCHAR(255) NULL,
          updated_by  VARCHAR(120) NULL,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (org_id, feature_key),
          CONSTRAINT fk_org_features_org FOREIGN KEY (org_id) REFERENCES organisations(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    add_column("organisations", "features_version", "INT NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS organisation_features")
    op.execute("ALTER TABLE organisations DROP COLUMN features_version")
