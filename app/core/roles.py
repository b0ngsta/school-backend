"""User roles and the groups the permission checks are written against."""

MANAGERS: tuple[str, ...] = ("admin", "principal", "sub_admin", "coordinator")
STAFF: tuple[str, ...] = MANAGERS + ("teacher",)
NON_STUDENT: tuple[str, ...] = STAFF + ("driver",)

# Accounts only an admin may create or delete.
ADMIN_MANAGED: tuple[str, ...] = ("admin", "principal", "sub_admin")
