"""Report cards — created by teachers, surveilled by admin, students see their own."""
import json

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import get_current_user, require_roles
from app.schemas import ReportCardCreate

router = APIRouter(prefix="/report-cards", tags=["report cards"])


@router.post("", status_code=201)
def create_report_card(
    body: ReportCardCreate, teacher: dict = Depends(require_roles("teacher"))
):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE id = %s AND user_type = 'student'",
            (body.student_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
        cur.execute(
            "SELECT id FROM report_cards WHERE student_id = %s AND term = %s",
            (body.student_id, body.term),
        )
        if cur.fetchone():
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Report card for this term already exists"
            )
        cur.execute(
            "INSERT INTO report_cards (student_id, teacher_id, term, grades, remarks) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                body.student_id,
                teacher["id"],
                body.term,
                json.dumps(body.grades) if body.grades is not None else None,
                body.remarks,
            ),
        )
        return {"id": cur.lastrowid}


def _decode(rows: list[dict]) -> list[dict]:
    for r in rows:
        if isinstance(r.get("grades"), str):
            r["grades"] = json.loads(r["grades"])
    return rows


@router.get("")
def list_report_cards(
    student_id: int | None = None, user: dict = Depends(get_current_user)
):
    """Admin/sub_admin: all (or filter by student). Teacher: ones they wrote. Student: own."""
    sql = (
        "SELECT rc.*, s.full_name AS student_name, t.full_name AS teacher_name, "
        "sp.roll_no, c.name AS class_name, sec.name AS section_name "
        "FROM report_cards rc "
        "JOIN users s ON s.id = rc.student_id "
        "JOIN users t ON t.id = rc.teacher_id "
        "LEFT JOIN student_profiles sp ON sp.user_id = rc.student_id "
        "LEFT JOIN classes c ON c.id = sp.class_id "
        "LEFT JOIN sections sec ON sec.id = sp.section_id"
    )
    where, params = [], []
    if user["user_type"] == "student":
        where.append("rc.student_id = %s")
        params.append(user["id"])
    elif user["user_type"] == "teacher":
        where.append("rc.teacher_id = %s")
        params.append(user["id"])
    if student_id is not None and user["user_type"] in ("admin", "sub_admin", "teacher"):
        where.append("rc.student_id = %s")
        params.append(student_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rc.created_at DESC"
    with db_cursor() as cur:
        cur.execute(sql, tuple(params))
        return _decode(cur.fetchall())


@router.delete("/{report_id}")
def delete_report_card(
    report_id: int, user: dict = Depends(require_roles("admin", "teacher"))
):
    with db_cursor() as cur:
        cur.execute("SELECT teacher_id FROM report_cards WHERE id = %s", (report_id,))
        rc = cur.fetchone()
        if not rc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Report card not found")
        if user["user_type"] == "teacher" and rc["teacher_id"] != user["id"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your report card")
        cur.execute("DELETE FROM report_cards WHERE id = %s", (report_id,))
    return {"deleted": report_id}
