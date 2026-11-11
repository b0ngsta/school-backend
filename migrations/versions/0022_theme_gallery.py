"""School theme colours and the school photo gallery.

* organisations.theme_color / header_color / button_color: the colours the
  school's admin picks on the School profile screen ("#RRGGBB"); NULL = the
  apps' built-in colour (header and buttons follow the main colour). Sent to
  the apps with every sign-in / token refresh.
* gallery_photos: school photos uploaded by the office, seen by everyone of
  that school (staff and parents).

Additive only.

Revision ID: 0022_theme_gallery
Revises: 0021_nov_requests
"""
from alembic import op

from migrations.helpers import add_column, has_column

revision = "0022_theme_gallery"
down_revision = "0021_nov_requests"
branch_labels = None
depends_on = None

THEME_COLUMNS = ("theme_color", "header_color", "button_color")


def upgrade() -> None:
    for column in THEME_COLUMNS:
        add_column("organisations", column, "VARCHAR(7) NULL")

    op.execute("""
        CREATE TABLE IF NOT EXISTS gallery_photos (
          id         INT          AUTO_INCREMENT PRIMARY KEY,
          org_id     INT          NOT NULL,
          image_path VARCHAR(255) NOT NULL,
          caption    VARCHAR(200) NULL,
          created_by INT          NULL,
          created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX ix_gallery_photos_org (org_id, id),
          CONSTRAINT fk_gallery_photos_org FOREIGN KEY (org_id) REFERENCES organisations(id),
          CONSTRAINT fk_gallery_photos_author FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gallery_photos")
    for column in THEME_COLUMNS:
        if has_column("organisations", column):
            op.execute(f"ALTER TABLE organisations DROP COLUMN {column}")
