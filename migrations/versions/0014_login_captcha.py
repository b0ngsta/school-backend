"""Sign-in protection that doesn't lock out a whole network.

Before this, 50 failed sign-ins from one IP address blocked every account
behind that address for 15 minutes, even with the right password. Mobile
carriers (Jio, Airtel) and school Wi-Fi put many people behind one address.

* auth_login_attempts.device_id: the app's install id (header X-Device-Id),
  so "too many tries" is counted per account and device, not per network.
* auth_captchas: one-time picture challenges. After a few failures on an
  account, or many from one address, sign-in needs the characters from a
  picture (POST /auth/captcha) instead of being refused. Only a keyed hash
  of the answer is stored.

Revision ID: 0014_login_captcha
Revises: 0013_keep_history
"""
from alembic import op

from migrations.helpers import add_column, add_index, drop_index, has_column

revision = "0014_login_captcha"
down_revision = "0013_keep_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column("auth_login_attempts", "device_id", "VARCHAR(64) NULL")
    add_index("auth_login_attempts", "idx_attempts_user_device_time", "username, device_id, created_at")
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth_captchas (
          id            CHAR(32)     NOT NULL PRIMARY KEY,
          answer_digest CHAR(64)     NOT NULL,
          ip            VARCHAR(45)  NULL,
          created_at    DATETIME(6)  NOT NULL,
          expires_at    DATETIME(6)  NOT NULL,
          used_at       DATETIME(6)  NULL,
          INDEX idx_captchas_ip_time (ip, created_at),
          INDEX idx_captchas_expiry (expires_at)
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_captchas")
    drop_index("auth_login_attempts", "idx_attempts_user_device_time")
    if has_column("auth_login_attempts", "device_id"):
        op.execute("ALTER TABLE auth_login_attempts DROP COLUMN device_id")
