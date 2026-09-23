"""User management — sub_admin (and admin) create/delete users, maintain teacher info."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import db_cursor
from app.deps import require_roles
from app.schemas import TeacherInfoUpdate, UserCreate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])

manager = require_roles("admin", "sub_admin")


@router.post("", status_code=201)
def create_user(body: UserCreate, actor: dict = Depends(manager)):
    # sub_admin cannot create admins
    if body.user_type == "admin" and actor["user_type"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admin can create admins")
    with db_cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (body.username,))
        if cur.fetchone():
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
        cur.execute(
            "INSERT INTO users (username, password_hash, full_name, email, phone, user_type) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                body.username,
                hash_password(body.password),
                body.full_name,
                body.email,
                body.phone,
                body.user_type,
            ),
        )
        user_id = cur.lastrowid
        if body.user_type == "teacher":
            info = body.teacher_info or TeacherInfoUpdate()
            cur.execute(
                "INSERT INTO teacher_profiles (user_id, subject, qualification, joining_date, address) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, info.subject, info.qualification, info.joining_date, info.address),
            )
        elif body.user_type == "student" and body.student_info:
            s = body.student_info
            cur.execute(
                "INSERT INTO student_profiles "
                "(user_id, class_id, section_id, roll_no, guardian_name, guardian_phone) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, s.class_id, s.section_id, s.roll_no, s.guardian_name, s.guardian_phone),
            )
    return {"id": user_id, "username": body.username, "user_type": body.user_type}


@router.get("")
def list_users(user_type: str | None = None, actor: dict = Depends(manager)):
    sql = (
        "SELECT id, username, full_name, email, phone, user_type, is_active, created_at "
        "FROM users"
    )
    params: tuple = ()
    if user_type:
        sql += " WHERE user_type = %s"
        params = (user_type,)
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY id", params)
        return cur.fetchall()


@router.delete("/{user_id}")
def delete_user(user_id: int, actor: dict = Depends(manager)):
    if user_id == actor["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete yourself")
    with db_cursor() as cur:
        cur.execute("SELECT user_type FROM users WHERE id = %s", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if target["user_type"] == "admin" and actor["user_type"] != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admin can delete admins")
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    return {"deleted": user_id}


# ---------- students (teachers need this list for report cards) ----------

@router.get("/students")
def list_students(
    actor: dict = Depends(require_roles("admin", "sub_admin", "teacher"))
):
    with db_cursor() as cur:
        cur.execute(
            "SELECT u.id, u.username, u.full_name, sp.roll_no, "
            "sp.class_id, c.name AS class_name, sp.section_id, s.name AS section_name "
            "FROM users u "
            "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
            "LEFT JOIN classes c ON c.id = sp.class_id "
            "LEFT JOIN sections s ON s.id = sp.section_id "
            "WHERE u.user_type = 'student' AND u.is_active = 1 ORDER BY u.full_name"
        )
        return cur.fetchall()


# ---------- teacher info ----------

@router.get("/teachers")
def list_teachers(actor: dict = Depends(require_roles("admin", "sub_admin"))):
    with db_cursor() as cur:
        cur.execute(
            "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.is_active, "
            "t.subject, t.qualification, t.joining_date, t.address "
            "FROM users u LEFT JOIN teacher_profiles t ON t.user_id = u.id "
            "WHERE u.user_type = 'teacher' ORDER BY u.full_name"
        )
        return cur.fetchall()


@router.put("/teachers/{user_id}")
def update_teacher_info(
    user_id: int, body: TeacherInfoUpdate, actor: dict = Depends(manager)
):
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE id = %s AND user_type = 'teacher'", (user_id,)
        )
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Teacher not found")
        cur.execute(
            "INSERT INTO teacher_profiles (user_id, subject, qualification, joining_date, address) "
            "VALUES (%s, %s, %s, %s, %s) AS new "
            "ON DUPLICATE KEY UPDATE subject=new.subject, qualification=new.qualification, "
            "joining_date=new.joining_date, address=new.address",
            (user_id, body.subject, body.qualification, body.joining_date, body.address),
        )
    return {"updated": user_id}
