"""Keep history when people leave: accounts are marked as left instead of
deleted, and the database itself refuses to wipe records with an account.

Before this, deleting a teacher also deleted (ON DELETE CASCADE) every
attendance row they had marked, their remarks, report cards, notices,
holidays, homework, exam papers, the salary records they entered and the SMS
log; deleting a student deleted their fees and payment proofs.

* users.left_on / left_reason: the day an account was marked as left (and
  why). A left account is inactive (is_active = 0) and can be reactivated.
* student_profiles.last_class_id / last_section_id: where a student was when
  they left. class_id / section_id become NULL, so the student drops out of
  every class list and frees their roll number; their records stay.
* users.principal_slot only counts a principal who has not left, so a school
  can appoint a new principal when the old one leaves.
* Foreign keys to users:
  - who wrote / marked / uploaded something (AUTHOR_COLUMNS) -> SET NULL:
    the record survives even if the account is ever removed;
  - who a record is about (OWNER_COLUMNS: fees, attendance, marks, salary…)
    -> RESTRICT: an account with history can't be deleted at all (the
    database answers "Record is still in use").
  Session, device, notification, profile and current-assignment rows keep
  CASCADE: they mean nothing without the account.

Revision ID: 0013_keep_history
Revises: 0012_driver_routine
"""
from alembic import op
from sqlalchemy import text

from migrations.helpers import add_column, add_index, drop_index, has_column

revision = "0013_keep_history"
down_revision = "0012_driver_routine"
branch_labels = None
depends_on = None

AUTHOR_COLUMNS = (
    ("attendance", "marked_by"),
    ("class_homework", "created_by"),
    ("enquiries", "created_by"),
    ("exam_papers", "uploaded_by"),
    ("holidays", "created_by"),
    ("homeworks", "created_by"),
    ("lesson_plans", "teacher_id"),
    ("notices", "created_by"),
    ("report_cards", "created_by"),
    ("salary_records", "created_by"),
    ("sms_messages", "created_by"),
    ("student_remarks", "created_by"),
)

OWNER_COLUMNS = (
    ("attendance", "student_id"),
    ("exam_results", "student_id"),
    ("fee_claims", "student_id"),
    ("fees", "student_id"),
    ("homework_submissions", "student_id"),
    ("homeworks", "student_id"),
    ("leave_requests", "user_id"),
    ("report_cards", "student_id"),
    ("salary_records", "user_id"),
    ("staff_attendance", "user_id"),
    ("student_remarks", "student_id"),
    ("substitution_requests", "requester_id"),
    ("substitution_requests", "substitute_id"),
    ("transactions", "student_id"),
)

# what each foreign key did before (for downgrade)
_PREVIOUS_RULE = {("transactions", "student_id"): "SET NULL"}

PRINCIPAL_NOW = "IF(user_type = 'principal' AND left_on IS NULL, 1, NULL)"
PRINCIPAL_BEFORE = "IF(user_type = 'principal', 1, NULL)"


def _bind():
    return op.get_bind()


def _user_fks(table: str, column: str) -> list[str]:
    rows = _bind().execute(text(
        "SELECT constraint_name FROM information_schema.key_column_usage "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c "
        "AND referenced_table_name = 'users'"
    ), {"t": table, "c": column})
    return [r[0] for r in rows]


def _column_type(table: str, column: str) -> str:
    return _bind().execute(text(
        "SELECT column_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar()


def _set_fk(table: str, column: str, rule: str, nullable: bool | None = None) -> None:
    """Point table.column at users(id) with ON DELETE `rule` under one known name."""
    for name in _user_fks(table, column):
        op.execute(f"ALTER TABLE {table} DROP FOREIGN KEY {name}")
    if nullable is not None:
        null = "NULL" if nullable else "NOT NULL"
        op.execute(f"ALTER TABLE {table} MODIFY COLUMN {column} {_column_type(table, column)} {null}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{column} "
               f"FOREIGN KEY ({column}) REFERENCES users(id) ON DELETE {rule}")


def _principal_slot(expression: str) -> None:
    drop_index("users", "uq_users_org_principal")
    if has_column("users", "principal_slot"):
        op.execute("ALTER TABLE users DROP COLUMN principal_slot")
    op.execute(f"ALTER TABLE users ADD COLUMN principal_slot TINYINT GENERATED ALWAYS AS ({expression}) VIRTUAL")
    add_index("users", "uq_users_org_principal", "org_id, principal_slot", unique=True)


def upgrade() -> None:
    add_column("users", "left_on", "DATE NULL")
    add_column("users", "left_reason", "VARCHAR(255) NULL")
    _principal_slot(PRINCIPAL_NOW)

    for column, target in (("last_class_id", "classes"), ("last_section_id", "sections")):
        add_column("student_profiles", column, "INT NULL")
        if not _bind().execute(text(
            "SELECT COUNT(*) FROM information_schema.table_constraints WHERE table_schema = DATABASE() "
            "AND table_name = 'student_profiles' AND constraint_name = :n"
        ), {"n": f"fk_student_profiles_{column}"}).scalar():
            op.execute(f"ALTER TABLE student_profiles ADD CONSTRAINT fk_student_profiles_{column} "
                       f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE SET NULL")

    # The rows already satisfy these keys (the old ones covered the same columns), so the new
    # keys are added without re-checking every row: MySQL then changes them in place instead of
    # copying each table, which matters for big attendance / fee tables.
    op.execute("SET SESSION foreign_key_checks = 0")
    try:
        for table, column in AUTHOR_COLUMNS:
            _set_fk(table, column, "SET NULL", nullable=True)
        for table, column in OWNER_COLUMNS:
            _set_fk(table, column, "RESTRICT")
    finally:
        op.execute("SET SESSION foreign_key_checks = 1")


def downgrade() -> None:
    for table, column in OWNER_COLUMNS:
        _set_fk(table, column, _PREVIOUS_RULE.get((table, column), "CASCADE"))
    for table, column in AUTHOR_COLUMNS:
        has_nulls = _bind().execute(text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")).scalar()
        _set_fk(table, column, "CASCADE", nullable=None if has_nulls else False)

    for column in ("last_section_id", "last_class_id"):
        name = f"fk_student_profiles_{column}"
        if _bind().execute(text(
            "SELECT COUNT(*) FROM information_schema.table_constraints WHERE table_schema = DATABASE() "
            "AND table_name = 'student_profiles' AND constraint_name = :n"
        ), {"n": name}).scalar():
            op.execute(f"ALTER TABLE student_profiles DROP FOREIGN KEY {name}")
        drop_index("student_profiles", name)
        if has_column("student_profiles", column):
            op.execute(f"ALTER TABLE student_profiles DROP COLUMN {column}")

    _principal_slot(PRINCIPAL_BEFORE)
    for column in ("left_reason", "left_on"):
        if has_column("users", column):
            op.execute(f"ALTER TABLE users DROP COLUMN {column}")
