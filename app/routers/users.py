"""Staff user management (admin / principal / sub_admin / teacher accounts).

Students are managed via /students (section pages).
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS, STAFF, ensure_can_edit_students
from app.schemas import StaffCreate, TeacherInfoUpdate
from app.security import hash_password
from app.uploads import IMAGE_TYPES, delete_file, save_upload

router = APIRouter(prefix="/users", tags=["users"])

manager = require_roles(*MANAGERS)
staff = require_roles(*STAFF)


@router.post("", status_code=201)
def create_staff(body: StaffCreate, actor: dict = Depends(manager)):
    # only admin can create admin/principal/sub_admin accounts
    if body.user_type in ("admin", "principal", "sub_admin") and actor["user_type"] != "admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only admin can create admin/principal/sub-admin accounts",
        )
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
    return {"id": user_id, "username": body.username, "user_type": body.user_type}


@router.get("")
def list_users(user_type: str | None = None, actor: dict = Depends(manager)):
    sql = (
        "SELECT id, username, full_name, email, phone, photo_path, user_type, is_active, created_at "
        "FROM users"
    )
    params: tuple = ()
    if user_type:
        sql += " WHERE user_type = %s"
        params = (user_type,)
    else:
        sql += " WHERE user_type != 'student'"
    with db_cursor() as cur:
        cur.execute(sql + " ORDER BY user_type, full_name", params)
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
        if target["user_type"] in ("admin", "principal") and actor["user_type"] != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admin can delete this account")
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    return {"deleted": user_id}


# ---------- profile photo (staff: managers; students: managers/class teacher) ----------

@router.post("/{user_id}/photo")
def upload_photo(
    user_id: int,
    file: UploadFile = File(...),
    actor: dict = Depends(staff),
):
    with db_cursor() as cur:
        cur.execute("SELECT id, user_type, photo_path FROM users WHERE id = %s", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if target["user_type"] == "student":
            cur.execute(
                "SELECT class_id, section_id FROM student_profiles WHERE user_id = %s",
                (user_id,),
            )
            sp = cur.fetchone() or {"class_id": None, "section_id": None}
            ensure_can_edit_students(cur, actor, sp["class_id"], sp["section_id"])
        elif actor["user_type"] not in MANAGERS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
        path = save_upload(file, "photos", IMAGE_TYPES, max_mb=5)
        delete_file(target["photo_path"])
        cur.execute("UPDATE users SET photo_path = %s WHERE id = %s", (path, user_id))
    return {"photo_path": path}


# ---------- teacher info ----------

@router.get("/teachers")
def list_teachers(actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT u.id, u.username, u.full_name, u.email, u.phone, u.photo_path, u.is_active, "
            "t.subject, t.qualification, t.joining_date, t.address, "
            "(SELECT COUNT(*) FROM lesson_plans lp WHERE lp.teacher_id = u.id) AS lesson_plan_count "
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
