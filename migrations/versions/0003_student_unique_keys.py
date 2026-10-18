"""Data integrity: admission numbers are unique school-wide and roll numbers
are unique within a section.

Blank values are stored as NULL first (several students without an
admission number are fine). If duplicates exist the migration stops before
changing anything and lists them, so they can be fixed in the app.

Revision ID: 0003_student_unique_keys
Revises: 0002_auth_audit_v8
"""
from alembic import op
from sqlalchemy import text

from migrations.helpers import add_index, drop_index

revision = "0003_student_unique_keys"
down_revision = "0002_auth_audit_v8"
branch_labels = None
depends_on = None


def _duplicates() -> list[str]:
    bind = op.get_bind()
    problems = []
    for row in bind.execute(text(
        "SELECT TRIM(sp.admission_no) AS value, GROUP_CONCAT(CONCAT(u.full_name, ' (id ', u.id, ')') "
        "SEPARATOR ', ') AS students "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE TRIM(sp.admission_no) <> '' GROUP BY TRIM(sp.admission_no) HAVING COUNT(*) > 1"
    )):
        problems.append(f"admission no {row.value!r}: {row.students}")
    for row in bind.execute(text(
        "SELECT c.name AS class_name, s.name AS section_name, TRIM(sp.roll_no) AS value, "
        "GROUP_CONCAT(CONCAT(u.full_name, ' (id ', u.id, ')') SEPARATOR ', ') AS students "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "JOIN sections s ON s.id = sp.section_id JOIN classes c ON c.id = s.class_id "
        "WHERE TRIM(sp.roll_no) <> '' "
        "GROUP BY sp.section_id, c.name, s.name, TRIM(sp.roll_no) HAVING COUNT(*) > 1"
    )):
        problems.append(f"{row.class_name}-{row.section_name} roll no {row.value!r}: {row.students}")
    return problems


def upgrade() -> None:
    problems = _duplicates()
    if problems:
        raise RuntimeError(
            "Cannot add unique keys; fix these duplicate student numbers first "
            "(edit the students in the app), then run `alembic upgrade head` again:\n  - "
            + "\n  - ".join(problems)
        )
    op.execute(
        "UPDATE student_profiles SET "
        "admission_no = NULLIF(TRIM(admission_no), ''), roll_no = NULLIF(TRIM(roll_no), '')"
    )
    add_index("student_profiles", "uq_student_admission_no", "admission_no", unique=True)
    add_index("student_profiles", "uq_student_section_roll", "section_id, roll_no", unique=True)


def downgrade() -> None:
    # MySQL dropped the implicit foreign-key index on section_id when the unique
    # key took over; put a plain one back before removing the unique key.
    add_index("student_profiles", "section_id", "section_id")
    drop_index("student_profiles", "uq_student_section_roll")
    drop_index("student_profiles", "uq_student_admission_no")
