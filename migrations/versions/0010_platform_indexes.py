"""Indexes for the platform (owner portal) queries that look across schools.

* auth_sessions(last_used_at): "active users in the last 24 h / 7 / 30 days",
  per school and in total, without scanning every session.
* staff_attendance(date, org_id): staff checked in today, grouped by school
  (the existing unique key starts with user_id, so a date filter can't use it).

Revision ID: 0010_platform_indexes
Revises: 0009_organisations
"""
from migrations.helpers import add_index, drop_index

revision = "0010_platform_indexes"
down_revision = "0009_organisations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_index("auth_sessions", "idx_sessions_last_used", "last_used_at")
    add_index("staff_attendance", "idx_staff_attendance_date_org", "date, org_id")


def downgrade() -> None:
    drop_index("staff_attendance", "idx_staff_attendance_date_org")
    drop_index("auth_sessions", "idx_sessions_last_used")
