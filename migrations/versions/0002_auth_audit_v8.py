"""v8: login sessions, login throttling, audit log, query indexes.

Same changes as migrations/legacy/migration_v8.sql, written so it is safe to
run whether or not that file was already applied by hand.

Revision ID: 0002_auth_audit_v8
Revises: 0001_baseline_v7
"""
from alembic import op

from migrations.helpers import add_column, add_index, drop_index, has_column

revision = "0002_auth_audit_v8"
down_revision = "0001_baseline_v7"
branch_labels = None
depends_on = None

# legacy salted-SHA256 hashes of the published seed passwords (admin123, …)
SEED_HASHES = (
    "seedsalt$0ee217e88b95c71bbbb2fa7f2903a08536b0af61f4ee2e707652e7f17d8b8899",
    "seedsalt$33bec7f81eb6495e135b65aa1a04733560a38d6a3a801438ddf78c11439a8a38",
    "seedsalt$5810ea5c2c101bcbb4a68bb2bdc55ed1b11ca37b483fd63ed6ebef095416f93d",
    "seedsalt$63bcf2a32d1dec275804108fc330aa42364c7876a7fb71fe3f609d3e2e1a5fa9",
    "seedsalt$8eb372cb36462e2851c080a901777c09b5d5e80e40771adba7f463c8b38db031",
)


def upgrade() -> None:
    # 1. users ---------------------------------------------------------
    newly_added = not has_column("users", "must_change_password")
    add_column("users", "token_version", "INT NOT NULL DEFAULT 0")
    add_column("users", "must_change_password", "TINYINT(1) NOT NULL DEFAULT 0")
    add_column("users", "password_changed_at", "DATETIME NULL")
    if newly_added:
        # accounts still using a published seed password must change it
        quoted = ", ".join(f"'{h}'" for h in SEED_HASHES)
        op.execute(f"UPDATE users SET must_change_password = 1 WHERE password_hash IN ({quoted})")

    # 2. login sessions (times are UTC) ----------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
          id                  CHAR(32)     NOT NULL PRIMARY KEY,
          user_id             INT          NOT NULL,
          salt                CHAR(64)     NOT NULL,
          generation          INT          NOT NULL DEFAULT 1,
          rotated_at          DATETIME(6)  NULL,
          created_at          DATETIME(6)  NOT NULL,
          last_used_at        DATETIME(6)  NOT NULL,
          expires_at          DATETIME(6)  NOT NULL,
          absolute_expires_at DATETIME(6)  NOT NULL,
          revoked_at          DATETIME(6)  NULL,
          revoke_reason       VARCHAR(30)  NULL,
          ip                  VARCHAR(45)  NULL,
          user_agent          VARCHAR(255) NULL,
          INDEX idx_sessions_user (user_id, last_used_at),
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)

    # 3. login attempts (times are UTC) ----------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth_login_attempts (
          id         BIGINT       AUTO_INCREMENT PRIMARY KEY,
          username   VARCHAR(100) NOT NULL,
          ip         VARCHAR(45)  NULL,
          success    TINYINT(1)   NOT NULL,
          created_at DATETIME(6)  NOT NULL,
          INDEX idx_attempts_user_time (username, created_at),
          INDEX idx_attempts_ip_time (ip, created_at),
          INDEX idx_attempts_time (created_at)
        ) ENGINE=InnoDB
    """)

    # 4. audit log ---------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
          id          BIGINT       AUTO_INCREMENT PRIMARY KEY,
          actor_id    INT          NULL,
          action      VARCHAR(60)  NOT NULL,
          entity_type VARCHAR(40)  NULL,
          entity_id   VARCHAR(64)  NULL,
          details     JSON         NULL,
          ip          VARCHAR(45)  NULL,
          request_id  VARCHAR(64)  NULL,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX idx_audit_actor (actor_id, created_at),
          INDEX idx_audit_entity (entity_type, entity_id),
          INDEX idx_audit_time (created_at)
        ) ENGINE=InnoDB
    """)

    # 5. indexes for the busiest queries ----------------------------------
    add_index("users", "idx_users_type_active", "user_type, is_active")
    add_index("attendance", "idx_attendance_date", "date")
    add_index("fees", "idx_fees_status_due", "status, due_date")
    add_index("fees", "idx_fees_student_title", "student_id, title")
    add_index("fee_claims", "idx_claims_fee_status", "fee_id, status")
    add_index("transactions", "idx_transactions_created", "created_at")


def downgrade() -> None:
    drop_index("transactions", "idx_transactions_created")
    # restore the plain foreign-key indexes the composite ones replaced
    add_index("fee_claims", "fee_id", "fee_id")
    add_index("fees", "student_id", "student_id")
    drop_index("fee_claims", "idx_claims_fee_status")
    drop_index("fees", "idx_fees_student_title")
    drop_index("fees", "idx_fees_status_due")
    drop_index("attendance", "idx_attendance_date")
    drop_index("users", "idx_users_type_active")
    op.execute("DROP TABLE IF EXISTS audit_logs")
    op.execute("DROP TABLE IF EXISTS auth_login_attempts")
    op.execute("DROP TABLE IF EXISTS auth_sessions")
    for column in ("password_changed_at", "must_change_password", "token_version"):
        if has_column("users", column):
            op.execute(f"ALTER TABLE users DROP COLUMN {column}")
