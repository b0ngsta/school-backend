"""Students and their academic records (homework, remarks, report cards).

Permissions:
- view: managers, or any teacher assigned to the student's class/section
- add/edit students: managers, or the section's CLASS teacher
- records: managers, or any teacher assigned to the section
"""
from __future__ import annotations

import json

from app.core.errors import BadRequest, Conflict, DuplicateKey, NotFound
from app.core.security import hash_password
from app.db import Db
from app.models.auth import CurrentUser
from app.models.students import (
    HomeworkCreate,
    HomeworkUpdate,
    RemarkCreate,
    ReportCardCreate,
    StudentCreate,
    StudentUpdate,
)
from app.services import access, audit, homework, pagination, storage

STUDENT_SELECT = (
    "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.photo_path, u.is_active, "
    "sp.class_id, sp.section_id, sp.roll_no, sp.admission_no, sp.dob, "
    "sp.father_name, sp.mother_name, sp.guardian_phone, sp.address, "
    "c.name AS class_name, s.name AS section_name "
    "FROM users u "
    "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
    "LEFT JOIN classes c ON c.id = sp.class_id "
    "LEFT JOIN sections s ON s.id = sp.section_id "
    "WHERE u.user_type = 'student'"
)

_PROFILE_FIELDS = ("roll_no", "admission_no", "dob", "father_name", "mother_name", "guardian_phone", "address")


def get_student(db: Db, student_id: int) -> dict:
    student = db.one(STUDENT_SELECT + " AND u.id = %s", (student_id,))
    if not student:
        raise NotFound("Student not found")
    return student


def _viewable_student(db: Db, user: CurrentUser, student_id: int) -> dict:
    student = get_student(db, student_id)
    access.ensure_can_view_section(db, user, student["class_id"], student["section_id"])
    return student


def _ensure_section_in_class(db: Db, class_id: int | None, section_id: int | None) -> None:
    if not db.one("SELECT id FROM sections WHERE id = %s AND class_id = %s", (section_id, class_id)):
        raise NotFound("Section not found in that class")


# ================= students =================

def create_student(db: Db, actor: CurrentUser, body: StudentCreate) -> dict:
    with db.transaction():
        access.ensure_can_edit_students(db, actor, body.class_id, body.section_id)
        _ensure_section_in_class(db, body.class_id, body.section_id)
        if db.one("SELECT id FROM users WHERE username = %s", (body.username,)):
            raise Conflict("Username already exists")
        try:
            student_id = db.execute(
                "INSERT INTO users (username, password_hash, full_name, email, phone, user_type) "
                "VALUES (%s, %s, %s, %s, %s, 'student')",
                (body.username, hash_password(body.password), body.full_name, body.email, body.phone),
            ).lastrowid
        except DuplicateKey:
            raise Conflict("Username already exists") from None
        db.execute(
            "INSERT INTO student_profiles "
            "(user_id, class_id, section_id, roll_no, admission_no, dob, "
            " father_name, mother_name, guardian_phone, address) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (student_id, body.class_id, body.section_id, body.roll_no, body.admission_no, body.dob,
             body.father_name, body.mother_name, body.guardian_phone, body.address),
        )
        audit.record(db, actor, "student.create", "user", student_id,
                     {"class_id": body.class_id, "section_id": body.section_id})
    return {"id": student_id, "username": body.username}


def list_students(db: Db, user: CurrentUser, class_id: int | None, section_id: int | None,
                  page: pagination.Page | None = None) -> pagination.PageResult:
    if user.user_type == "teacher":
        if class_id is None or section_id is None:
            raise BadRequest("class_id and section_id required")
        access.ensure_can_view_section(db, user, class_id, section_id)
    sql, params = STUDENT_SELECT, []
    if class_id is not None:
        sql += " AND sp.class_id = %s"
        params.append(class_id)
    if section_id is not None:
        sql += " AND sp.section_id = %s"
        params.append(section_id)
    return pagination.fetch(db, sql, params, "CAST(sp.roll_no AS UNSIGNED), u.full_name, u.id", page)


def view_student(db: Db, user: CurrentUser, student_id: int) -> dict:
    return _viewable_student(db, user, student_id)


def update_student(db: Db, user: CurrentUser, student_id: int, body: StudentUpdate) -> dict:
    """Fields left out of the request keep their current value. Moving a
    student needs edit rights on both the old and the new section."""
    changes = body.model_dump(exclude_unset=True)
    with db.transaction():
        student = get_student(db, student_id)
        access.ensure_can_edit_students(db, user, student["class_id"], student["section_id"])

        new_class = changes.get("class_id") if changes.get("class_id") is not None else student["class_id"]
        new_section = changes.get("section_id") if changes.get("section_id") is not None else student["section_id"]
        if (new_class, new_section) != (student["class_id"], student["section_id"]):
            _ensure_section_in_class(db, new_class, new_section)
            access.ensure_can_edit_students(db, user, new_class, new_section)

        merged = {**student, **changes}
        db.execute(
            "UPDATE users SET full_name = %s, email = %s, phone = %s WHERE id = %s",
            (merged["full_name"], merged["email"], merged["phone"], student_id),
        )
        db.execute(
            "UPDATE student_profiles SET class_id=%s, section_id=%s, roll_no=%s, "
            "admission_no=%s, dob=%s, father_name=%s, mother_name=%s, "
            "guardian_phone=%s, address=%s WHERE user_id = %s",
            (new_class, new_section, *(merged[f] for f in _PROFILE_FIELDS), student_id),
        )
        audit.record(db, user, "student.update", "user", student_id,
                     {"fields": sorted(changes), "class_id": new_class, "section_id": new_section})
    return {"updated": student_id}


def delete_student(db: Db, user: CurrentUser, student_id: int) -> dict:
    with db.transaction():
        student = get_student(db, student_id)
        access.ensure_can_edit_students(db, user, student["class_id"], student["section_id"])
        db.execute("DELETE FROM users WHERE id = %s", (student_id,))
        storage.delete_after_commit(db, student["photo_path"])
        audit.record(db, user, "student.delete", "user", student_id,
                     {"username": student["username"], "full_name": student["full_name"]})
    return {"deleted": student_id}


# ================= attendance history =================

def attendance_history(db: Db, user: CurrentUser, student_id: int) -> dict:
    _viewable_student(db, user, student_id)
    records = db.all(
        "SELECT date, status FROM attendance WHERE student_id = %s ORDER BY date DESC LIMIT 30",
        (student_id,),
    )
    agg = db.one(
        "SELECT SUM(status='present') AS present, COUNT(*) AS total FROM attendance WHERE student_id = %s",
        (student_id,),
    )
    total = int(agg["total"] or 0)
    return {
        "records": records,
        "percentage": round(int(agg["present"] or 0) / total * 100, 1) if total else None,
        "total_marked": total,
    }


# ================= homework =================

def list_homework(db: Db, user: CurrentUser, student_id: int) -> list[dict]:
    """Class homework of the student's section merged with per-student rows,
    newest first. `scope` is "class" or "student"; for class rows `id` is the
    class homework id (clients key rows by scope + id)."""
    _viewable_student(db, user, student_id)
    return db.all(
        homework.student_homework_sql("hw.*") + " ORDER BY " + homework.HOMEWORK_ORDER,
        (student_id, student_id, student_id),
    )


def add_homework(db: Db, user: CurrentUser, student_id: int, body: HomeworkCreate) -> dict:
    _viewable_student(db, user, student_id)
    homework_id = db.execute(
        "INSERT INTO homeworks (student_id, title, due_date, status, note, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (student_id, body.title, body.due_date, body.status, body.note, user.id),
    ).lastrowid
    return {"id": homework_id}


def update_homework(db: Db, user: CurrentUser, student_id: int, homework_id: int, body: HomeworkUpdate,
                    scope: str | None = None) -> dict:
    if scope == "class":  # this student's status on a class homework
        with db.transaction():
            _viewable_student(db, user, student_id)
            if body.status is None:
                raise BadRequest("status is required")
            if not homework.class_homework_for_student(db, student_id, homework_id):
                raise NotFound("Homework not found")
            homework.set_status(db, user, homework_id, [(student_id, body.status)])
        return {"updated": homework_id}
    with db.transaction():
        _viewable_student(db, user, student_id)
        hw = db.one("SELECT * FROM homeworks WHERE id = %s AND student_id = %s FOR UPDATE",
                    (homework_id, student_id))
        if not hw:
            raise NotFound("Homework not found")
        db.execute(
            "UPDATE homeworks SET title=%s, due_date=%s, status=%s, note=%s WHERE id=%s",
            (
                body.title if body.title is not None else hw["title"],
                body.due_date if body.due_date is not None else hw["due_date"],
                body.status if body.status is not None else hw["status"],
                body.note if body.note is not None else hw["note"],
                homework_id,
            ),
        )
    return {"updated": homework_id}


def delete_homework(db: Db, user: CurrentUser, student_id: int, homework_id: int,
                    scope: str | None = None) -> dict:
    _viewable_student(db, user, student_id)
    if scope == "class":
        raise Conflict("Class homework is deleted from the class's homework list, not per student")
    if db.execute("DELETE FROM homeworks WHERE id = %s AND student_id = %s",
                  (homework_id, student_id)).rowcount == 0:
        raise NotFound("Homework not found")
    return {"deleted": homework_id}


# ================= remarks =================

def list_remarks(db: Db, user: CurrentUser, student_id: int) -> list[dict]:
    _viewable_student(db, user, student_id)
    return db.all(
        "SELECT r.*, u.full_name AS created_by_name FROM student_remarks r "
        "JOIN users u ON u.id = r.created_by "
        "WHERE r.student_id = %s ORDER BY r.created_at DESC",
        (student_id,),
    )


def add_remark(db: Db, user: CurrentUser, student_id: int, body: RemarkCreate) -> dict:
    _viewable_student(db, user, student_id)
    remark_id = db.execute(
        "INSERT INTO student_remarks (student_id, remark, created_by) VALUES (%s, %s, %s)",
        (student_id, body.remark, user.id),
    ).lastrowid
    return {"id": remark_id}


def delete_remark(db: Db, user: CurrentUser, student_id: int, remark_id: int) -> dict:
    _viewable_student(db, user, student_id)
    if db.execute("DELETE FROM student_remarks WHERE id = %s AND student_id = %s",
                  (remark_id, student_id)).rowcount == 0:
        raise NotFound("Remark not found")
    return {"deleted": remark_id}


# ================= report cards =================

def list_report_cards(db: Db, user: CurrentUser, student_id: int) -> list[dict]:
    _viewable_student(db, user, student_id)
    rows = db.all(
        "SELECT rc.*, u.full_name AS created_by_name FROM report_cards rc "
        "JOIN users u ON u.id = rc.created_by "
        "WHERE rc.student_id = %s ORDER BY rc.created_at DESC",
        (student_id,),
    )
    for r in rows:
        if isinstance(r.get("grades"), str):
            r["grades"] = json.loads(r["grades"])
    return rows


def add_report_card(db: Db, user: CurrentUser, student_id: int, body: ReportCardCreate) -> dict:
    with db.transaction():
        _viewable_student(db, user, student_id)
        if db.one("SELECT id FROM report_cards WHERE student_id = %s AND term = %s", (student_id, body.term)):
            raise Conflict("Report card for this term exists")
        try:
            report_id = db.execute(
                "INSERT INTO report_cards (student_id, created_by, term, grades, remarks) "
                "VALUES (%s, %s, %s, %s, %s)",
                (student_id, user.id, body.term,
                 json.dumps(body.grades) if body.grades is not None else None, body.remarks),
            ).lastrowid
        except DuplicateKey:
            raise Conflict("Report card for this term exists") from None
        audit.record(db, user, "report_card.create", "report_card", report_id,
                     {"student_id": student_id, "term": body.term})
    return {"id": report_id}


def delete_report_card(db: Db, user: CurrentUser, student_id: int, report_id: int) -> dict:
    with db.transaction():
        _viewable_student(db, user, student_id)
        if db.execute("DELETE FROM report_cards WHERE id = %s AND student_id = %s",
                      (report_id, student_id)).rowcount == 0:
            raise NotFound("Report card not found")
        audit.record(db, user, "report_card.delete", "report_card", report_id, {"student_id": student_id})
    return {"deleted": report_id}
