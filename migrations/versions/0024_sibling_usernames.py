"""Siblings: student accounts of one school may share a username.

A parent signs in to the app as their child. Brothers and sisters get the
same username from the office (usually a parent's mobile number), each with
their own password, and the app can switch between them (services/siblings.py).

* users.staff_username: a virtual column that is the username for every
  account EXCEPT students (NULL for students), with a UNIQUE index per
  school. The database itself still refuses two staff / driver accounts with
  one username (left accounts included, as before), while student rows no
  longer take part in that rule. The same technique as users.principal_slot.
* ix_users_org_username: a plain index for sign-in lookups by username.
* uq_users_org_username (one username = one account per school) is dropped.

"A student never shares a username with a non-student" is checked in the
service (MySQL has no index for it). No row is changed: until the office
gives two students the same username, everything is exactly as before.

Downgrade stops, listing them, while any username is shared: the old unique
key cannot be restored until those accounts have different usernames.

Revision ID: 0024_sibling_usernames
Revises: 0023_live_tracking
"""
from alembic import op
from sqlalchemy import text

from migrations.helpers import add_index, drop_index, has_column

revision = "0024_sibling_usernames"
down_revision = "0023_live_tracking"
branch_labels = None
depends_on = None

STAFF_USERNAME = "IF(user_type = 'student', NULL, username)"


def _username_type() -> str:
    """The exact type of users.username, so both columns compare names the same way."""
    row = op.get_bind().execute(text(
        "SELECT CHARACTER_MAXIMUM_LENGTH AS len, CHARACTER_SET_NAME AS cs, COLLATION_NAME AS coll "
        "FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'username'"
    )).one()
    return f"VARCHAR({int(row.len)}) CHARACTER SET {row.cs} COLLATE {row.coll}"


def _shared_usernames() -> list[str]:
    rows = op.get_bind().execute(text(
        "SELECT o.code AS code, u.username AS username, "
        "GROUP_CONCAT(CONCAT(u.full_name, ' (id ', u.id, ')') ORDER BY u.id SEPARATOR ', ') AS who "
        "FROM users u JOIN organisations o ON o.id = u.org_id "
        "GROUP BY o.code, u.org_id, u.username HAVING COUNT(*) > 1 ORDER BY o.code, u.username"
    ))
    return [f"school {r.code}, username {r.username!r}: {r.who}" for r in rows]


def upgrade() -> None:
    # new rules first, so staff usernames are unique at every moment
    add_index("users", "ix_users_org_username", "org_id, username")
    if not has_column("users", "staff_username"):
        op.execute(f"ALTER TABLE users ADD COLUMN staff_username {_username_type()} "
                   f"GENERATED ALWAYS AS ({STAFF_USERNAME}) VIRTUAL")
    add_index("users", "uq_users_org_staff_username", "org_id, staff_username", unique=True)
    drop_index("users", "uq_users_org_username")


def downgrade() -> None:
    shared = _shared_usernames()
    if shared:
        raise RuntimeError(
            "Some accounts share a username (brothers and sisters). The older schema allows one account "
            "per username, so give these accounts different usernames first, then downgrade again:\n  - "
            + "\n  - ".join(shared)
        )
    add_index("users", "uq_users_org_username", "org_id, username", unique=True)
    drop_index("users", "uq_users_org_staff_username")
    if has_column("users", "staff_username"):
        op.execute("ALTER TABLE users DROP COLUMN staff_username")
    drop_index("users", "ix_users_org_username")
