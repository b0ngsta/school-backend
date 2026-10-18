"""Password reset by emailed code, and "only one principal".

* password_reset_codes: one row per emailed code (only a keyed hash of the
  code is stored), with expiry and a wrong-guess counter.
* auth_reset_requests: every "forgot password" request (the username or
  email typed, and the caller's address) for rate limiting; kept 30 days,
  like login attempts.
* users.email index: reset requests may look accounts up by email.
* users.principal_slot: a virtual column that is 1 for the principal and
  NULL for everyone else, with a UNIQUE index — the database itself refuses
  a second principal, even if two admins submit at the same moment. If the
  database already has more than one principal the migration stops before
  changing anything and lists them.

Revision ID: 0004_password_reset_principal
Revises: 0003_student_unique_keys
"""
from alembic import op
from sqlalchemy import text

from migrations.helpers import add_column, add_index, drop_index, has_column

revision = "0004_password_reset_principal"
down_revision = "0003_student_unique_keys"
branch_labels = None
depends_on = None


def _principals() -> list[str]:
    rows = op.get_bind().execute(text(
        "SELECT id, username, full_name FROM users WHERE user_type = 'principal' ORDER BY id"
    ))
    return [f"{r.full_name} (username {r.username!r}, id {r.id})" for r in rows]


def upgrade() -> None:
    principals = _principals()
    if len(principals) > 1:
        raise RuntimeError(
            "Only one principal is allowed, but the database has several. Delete the extra "
            "account(s) or ask an admin to recreate them with another role, then run "
            "`alembic upgrade head` again:\n  - " + "\n  - ".join(principals)
        )

    op.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_codes (
          id          BIGINT       AUTO_INCREMENT PRIMARY KEY,
          user_id     INT          NOT NULL,
          code_hash   CHAR(64)     NOT NULL,
          created_at  DATETIME(6)  NOT NULL,
          expires_at  DATETIME(6)  NOT NULL,
          attempts    INT          NOT NULL DEFAULT 0,
          used_at     DATETIME(6)  NULL,
          ip          VARCHAR(45)  NULL,
          INDEX idx_reset_codes_user (user_id, created_at),
          INDEX idx_reset_codes_created (created_at),
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth_reset_requests (
          id          BIGINT       AUTO_INCREMENT PRIMARY KEY,
          login       VARCHAR(120) NOT NULL,
          ip          VARCHAR(45)  NULL,
          created_at  DATETIME(6)  NOT NULL,
          INDEX idx_reset_requests_login (login, created_at),
          INDEX idx_reset_requests_ip (ip, created_at),
          INDEX idx_reset_requests_created (created_at)
        ) ENGINE=InnoDB
    """)
    add_index("users", "idx_users_email", "email")
    add_column("users", "principal_slot",
               "TINYINT GENERATED ALWAYS AS (IF(user_type = 'principal', 1, NULL)) VIRTUAL")
    add_index("users", "uq_users_single_principal", "principal_slot", unique=True)


def downgrade() -> None:
    drop_index("users", "uq_users_single_principal")
    if has_column("users", "principal_slot"):
        op.execute("ALTER TABLE users DROP COLUMN principal_slot")
    drop_index("users", "idx_users_email")
    op.execute("DROP TABLE IF EXISTS auth_reset_requests")
    op.execute("DROP TABLE IF EXISTS password_reset_codes")
