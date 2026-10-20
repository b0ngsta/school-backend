"""In-app + push notifications.

* notifications: one row per recipient (a parent's notifications go to the
  student account). `data` says where tapping it goes in the apps.
* device_tokens: FCM registration tokens of signed-in devices (with the
  login session they were registered from, so logging out removes them).
* fee_reminders_sent: which "due soon" / "overdue" reminders went out, so
  the daily job never sends one twice.

Revision ID: 0005_notifications
Revises: 0004_password_reset_principal
"""
from alembic import op

revision = "0005_notifications"
down_revision = "0004_password_reset_principal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
          id          BIGINT       AUTO_INCREMENT PRIMARY KEY,
          user_id     INT          NOT NULL,
          type        VARCHAR(40)  NOT NULL,
          title       VARCHAR(200) NOT NULL,
          body        TEXT         NULL,
          data        JSON         NULL,
          read_at     TIMESTAMP    NULL,
          created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX ix_notifications_user_unread (user_id, read_at, created_at),
          INDEX ix_notifications_user_created (user_id, created_at),
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS device_tokens (
          id            INT          AUTO_INCREMENT PRIMARY KEY,
          user_id       INT          NOT NULL,
          token         VARCHAR(512) NOT NULL,
          platform      VARCHAR(10)  NOT NULL,
          session_id    CHAR(32)     NULL,
          created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_seen_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_device_token (token),
          INDEX ix_device_tokens_user (user_id),
          INDEX ix_device_tokens_session (session_id),
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS fee_reminders_sent (
          fee_id   INT         NOT NULL,
          kind     VARCHAR(10) NOT NULL,
          sent_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (fee_id, kind),
          FOREIGN KEY (fee_id) REFERENCES fees(id) ON DELETE CASCADE
        ) ENGINE=InnoDB
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fee_reminders_sent")
    op.execute("DROP TABLE IF EXISTS device_tokens")
    op.execute("DROP TABLE IF EXISTS notifications")
