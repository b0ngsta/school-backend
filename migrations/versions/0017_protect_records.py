"""Deleting a class, section, exam or fee can no longer erase records.

Before this, these foreign keys were ON DELETE CASCADE, so one click wiped
history: deleting a section erased its attendance, class homework, timetable /
syllabus documents and substitution records; deleting a class did that for all
its sections plus its lesson plans and every mark in its exams; deleting an
exam erased every result; deleting a fee erased the parents' payment proofs.

Now they are RESTRICT: the database refuses such a delete ("Record is still
in use"). The API checks first and explains what is attached
(services/classes.py, exams.py, fees.py); deleting something with no records
works exactly as before. Keys for current set-up (timetable slots, teacher
assignments, class subjects, exam question papers, fee reminder log, transport
riders) keep CASCADE; links that only point at a class (students, messages,
lesson plans' section) keep SET NULL. No rows change.

Revision ID: 0017_protect_records
Revises: 0016_durable_jobs
"""
from alembic import op
from sqlalchemy import text

revision = "0017_protect_records"
down_revision = "0016_durable_jobs"
branch_labels = None
depends_on = None

# (table, column, referenced table): records that a parent delete used to erase
PROTECTED = (
    ("attendance", "section_id", "sections"),
    ("class_homework", "class_id", "classes"),
    ("class_homework", "section_id", "sections"),
    ("school_documents", "class_id", "classes"),
    ("school_documents", "section_id", "sections"),
    ("substitution_requests", "class_id", "classes"),
    ("substitution_requests", "section_id", "sections"),
    ("lesson_plans", "class_id", "classes"),
    ("exam_results", "exam_id", "exams"),
    ("fee_claims", "fee_id", "fees"),
)


def _fks(table: str, column: str, target: str) -> list[str]:
    rows = op.get_bind().execute(text(
        "SELECT constraint_name FROM information_schema.key_column_usage "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c "
        "AND referenced_table_name = :r"
    ), {"t": table, "c": column, "r": target})
    return [r[0] for r in rows]


def _set_fk(table: str, column: str, target: str, rule: str) -> None:
    """Point table.column at target(id) with ON DELETE `rule` under one known name."""
    for name in _fks(table, column, target):
        op.execute(f"ALTER TABLE {table} DROP FOREIGN KEY {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{column} "
               f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {rule}")


def _change(rule: str) -> None:
    # Same columns, same rows: re-adding the keys without re-checking every row lets MySQL
    # change them in place instead of copying big tables (attendance).
    op.execute("SET SESSION foreign_key_checks = 0")
    try:
        for table, column, target in PROTECTED:
            _set_fk(table, column, target, rule)
    finally:
        op.execute("SET SESSION foreign_key_checks = 1")


def upgrade() -> None:
    _change("RESTRICT")


def downgrade() -> None:
    _change("CASCADE")
