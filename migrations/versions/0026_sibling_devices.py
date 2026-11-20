"""One phone, every brother and sister (Nov 2026).

device_tokens was unique on `token`: a phone belonged to exactly one account,
so after "Switch child" only the child signed in got pushes. Now the same
token may be registered for several accounts (the active students who share
the username, services/notifications.register_device); the unique key is
(token, user_id). Nothing else changes, no row changes.

Downgrade keeps one row per token (the most recently seen) and puts the old
unique key back.

Revision ID: 0026_sibling_devices
Revises: 0025_print_templates
"""
from alembic import op

revision = "0026_sibling_devices"
down_revision = "0025_print_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # the new key starts with token, so lookups / deletes by token keep an index
    op.execute("ALTER TABLE device_tokens ADD UNIQUE KEY uq_device_token_user (token, user_id)")
    op.execute("ALTER TABLE device_tokens DROP INDEX uq_device_token")


def downgrade() -> None:
    op.execute("""
        DELETE d FROM device_tokens d
        JOIN device_tokens newer ON newer.token = d.token
         AND (newer.last_seen_at > d.last_seen_at OR (newer.last_seen_at = d.last_seen_at AND newer.id > d.id))
    """)
    op.execute("ALTER TABLE device_tokens ADD UNIQUE KEY uq_device_token (token)")
    op.execute("ALTER TABLE device_tokens DROP INDEX uq_device_token_user")
