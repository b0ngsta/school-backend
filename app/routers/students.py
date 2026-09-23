"""Students + their academic records (homework, remarks, report cards).

Permissions:
- view: managers, or any teacher assigned to the student's class/section
- add/edit students: managers, or the section's CLASS teacher
- records: managers, or any teacher assigned to the section
"""
import json

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import get_current_user, require_roles
from app.permissions import MANAGERS, STAFF, ensure_can_edit_students, ensure_can_view_section
from app.schemas import (
    HomeworkCreate,
    HomeworkUpdate,
    RemarkCreate,
    ReportCardCreate,
    StudentCreate,
    StudentUpdate,
)
from app.security import hash_password

router = APIRouter(prefix="/students", tags=["students"])

staff = require_roles(*STAFF)

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


def _get_student(cur, student_id: int) -> dict:
    cur.execute(STUDENT_SELECT + " AND u.id = %s", (student_id,))
    student = cur.fetchone()
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    return student


@router.post("", status_code=201)
def create_student(body: StudentCreate, actor: dict = Depends(staff)):
    with db_cursor() as cur:
        ensure_can_edit_students(cur, actor, body.class_id, body.section_id)
        cur.execute(
            "SELECT id FROM sections WHERE id = %s AND class_id = %s",
            (body.section_id, body.class_id),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found in that class")
        cur.execute("SELECT id FROM users WHERE username = %s", (body.username,))
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
        cur.execute(
            "INSERT INTO users (username, password_hash, full_name, email, phone, user_type) "
            "VALUES (%s, %s, %s, %s, %s, 'student')",
            (body.username, hash_password(body.password), body.full_name, body.email, body.phone),
        )
        student_id = cur.lastrowid
        cur.execute(
            "INSERT INTO student_profiles "
            "(user_id, class_id, section_id, roll_no, admission_no, dob, "
            " father_name, mother_name, guardian_phone, address) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                student_id, body.class_id, body.section_id, body.roll_no,
                body.admission_no, body.dob, body.father_name, body.mother_name,
                body.guardian_phone, body.address,
            ),
        )
    return {"id": student_id, "username": body.username}


@router.get("")
def list_students(
    class_id: int | None = None,
    section_id: int | None = None,
    user: dict = Depends(staff),
):
    with db_cursor() as cur:
        if user["user_type"] == "teacher":
            if class_id is None or section_id is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "class_id and section_id required"
                )
            ensure_can_view_section(cur, user, class_id, section_id)
        sql = STUDENT_SELECT
        params: list = []
        if class_id is not None:
            sql += " AND sp.class_id = %s"
            params.append(class_id)
        if section_id is not None:
            sql += " AND sp.section_id = %s"
            params.append(section_id)
        cur.execute(sql + " ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name", tuple(params))
        return cur.fetchall()


@router.get("/{student_id}")
def get_student(student_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        return student


@router.put("/{student_id}")
def update_student(student_id: int, body: StudentUpdate, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_edit_students(cur, user, student["class_id"], student["section_id"])
        new_class = body.class_id if body.class_id is not None else student["class_id"]
        new_section = body.section_id if body.section_id is not None else student["section_id"]
        cur.execute(
            "UPDATE users SET full_name = %s, email = %s, phone = %s WHERE id = %s",
            (body.full_name, body.email, body.phone, student_id),
        )
        cur.execute(
            "UPDATE student_profiles SET class_id=%s, section_id=%s, roll_no=%s, "
            "admission_no=%s, dob=%s, father_name=%s, mother_name=%s, "
            "guardian_phone=%s, address=%s WHERE user_id = %s",
            (
                new_class, new_section, body.roll_no, body.admission_no, body.dob,
                body.father_name, body.mother_name, body.guardian_phone,
                body.address, student_id,
            ),
        )
    return {"updated": student_id}


@router.delete("/{student_id}")
def delete_student(student_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_edit_students(cur, user, student["class_id"], student["section_id"])
        cur.execute("DELETE FROM users WHERE id = %s", (student_id,))
    return {"deleted": student_id}


# ================= attendance history =================

@router.get("/{student_id}/attendance")
def student_attendance(student_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "SELECT date, status FROM attendance WHERE student_id = %s "
            "ORDER BY date DESC LIMIT 30",
            (student_id,),
        )
        records = cur.fetchall()
        cur.execute(
            "SELECT SUM(status='present') AS present, COUNT(*) AS total "
            "FROM attendance WHERE student_id = %s",
            (student_id,),
        )
        agg = cur.fetchone()
    total = int(agg["total"] or 0)
    return {
        "records": records,
        "percentage": round(int(agg["present"] or 0) / total * 100, 1) if total else None,
        "total_marked": total,
    }


# ================= homework =================

@router.get("/{student_id}/homework")
def list_homework(student_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "SELECT h.*, u.full_name AS created_by_name FROM homeworks h "
            "JOIN users u ON u.id = h.created_by "
            "WHERE h.student_id = %s ORDER BY h.created_at DESC",
            (student_id,),
        )
        return cur.fetchall()


@router.post("/{student_id}/homework", status_code=201)
def add_homework(student_id: int, body: HomeworkCreate, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "INSERT INTO homeworks (student_id, title, due_date, status, note, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (student_id, body.title, body.due_date, body.status, body.note, user["id"]),
        )
        return {"id": cur.lastrowid}


@router.put("/{student_id}/homework/{homework_id}")
def update_homework(
    student_id: int, homework_id: int, body: HomeworkUpdate, user: dict = Depends(staff)
):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "SELECT * FROM homeworks WHERE id = %s AND student_id = %s",
            (homework_id, student_id),
        )
        hw = cur.fetchone()
        if not hw:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Homework not found")
        cur.execute(
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


@router.delete("/{student_id}/homework/{homework_id}")
def delete_homework(student_id: int, homework_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "DELETE FROM homeworks WHERE id = %s AND student_id = %s",
            (homework_id, student_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Homework not found")
    return {"deleted": homework_id}


# ================= remarks =================

@router.get("/{student_id}/remarks")
def list_remarks(student_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "SELECT r.*, u.full_name AS created_by_name FROM student_remarks r "
            "JOIN users u ON u.id = r.created_by "
            "WHERE r.student_id = %s ORDER BY r.created_at DESC",
            (student_id,),
        )
        return cur.fetchall()


@router.post("/{student_id}/remarks", status_code=201)
def add_remark(student_id: int, body: RemarkCreate, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "INSERT INTO student_remarks (student_id, remark, created_by) VALUES (%s, %s, %s)",
            (student_id, body.remark, user["id"]),
        )
        return {"id": cur.lastrowid}


@router.delete("/{student_id}/remarks/{remark_id}")
def delete_remark(student_id: int, remark_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "DELETE FROM student_remarks WHERE id = %s AND student_id = %s",
            (remark_id, student_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Remark not found")
    return {"deleted": remark_id}


# ================= report cards =================

@router.get("/{student_id}/report-cards")
def list_report_cards(student_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "SELECT rc.*, u.full_name AS created_by_name FROM report_cards rc "
            "JOIN users u ON u.id = rc.created_by "
            "WHERE rc.student_id = %s ORDER BY rc.created_at DESC",
            (student_id,),
        )
        rows = cur.fetchall()
    for r in rows:
        if isinstance(r.get("grades"), str):
            r["grades"] = json.loads(r["grades"])
    return rows


@router.post("/{student_id}/report-cards", status_code=201)
def add_report_card(student_id: int, body: ReportCardCreate, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "SELECT id FROM report_cards WHERE student_id = %s AND term = %s",
            (student_id, body.term),
        )
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Report card for this term exists")
        cur.execute(
            "INSERT INTO report_cards (student_id, created_by, term, grades, remarks) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                student_id, user["id"], body.term,
                json.dumps(body.grades) if body.grades is not None else None,
                body.remarks,
            ),
        )
        return {"id": cur.lastrowid}


@router.delete("/{student_id}/report-cards/{report_id}")
def delete_report_card(student_id: int, report_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        student = _get_student(cur, student_id)
        ensure_can_view_section(cur, user, student["class_id"], student["section_id"])
        cur.execute(
            "DELETE FROM report_cards WHERE id = %s AND student_id = %s",
            (report_id, student_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Report card not found")
    return {"deleted": report_id}
