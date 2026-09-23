"""Exams — schedule, subject-wise question papers, per-student results.

- staff can view exams, sections and results
- results entry: managers + teachers assigned to the section
- question paper upload: admin / sub_admin / coordinator / principal
- exams with class_id = NULL apply to ALL classes
- papers are per exam + class + subject (see exam_papers table)
"""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from app.database import db_cursor
from app.deps import require_roles
from app.permissions import MANAGERS, STAFF, ensure_can_view_section, is_assigned
from app.uploads import PDF_TYPES, delete_file, save_upload

router = APIRouter(prefix="/exams", tags=["exams"])

manager = require_roles(*MANAGERS)
paper_manager = require_roles("admin", "sub_admin", "coordinator", "principal")
staff = require_roles(*STAFF)

EXAM_STATUS = (
    "CASE WHEN CURDATE() < e.start_date THEN 'upcoming' "
    "WHEN CURDATE() > e.end_date THEN 'completed' ELSE 'ongoing' END"
)


class ExamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    class_id: int | None = None  # None = exam applies to ALL classes
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def check_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on/after start_date")
        return self


class ExamUpdate(BaseModel):
    name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    results_published: bool | None = None


class ResultEntry(BaseModel):
    student_id: int
    marks: float | None = None
    grade: str | None = Field(default=None, max_length=10)
    remarks: str | None = Field(default=None, max_length=255)


class ResultsSave(BaseModel):
    section_id: int
    results: list[ResultEntry]


def _get_exam(cur, exam_id: int) -> dict:
    cur.execute(
        f"SELECT e.*, c.name AS class_name, {EXAM_STATUS} AS status "
        "FROM exams e LEFT JOIN classes c ON c.id = e.class_id WHERE e.id = %s",
        (exam_id,),
    )
    exam = cur.fetchone()
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    return exam


def _exam_applies_to_class(exam: dict, class_id: int) -> bool:
    return exam["class_id"] is None or exam["class_id"] == class_id


@router.get("/stats")
def exam_stats(user: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            f"SELECT "
            f"SUM({EXAM_STATUS} = 'upcoming') AS upcoming, "
            f"SUM({EXAM_STATUS} = 'ongoing') AS ongoing, "
            f"SUM({EXAM_STATUS} = 'completed') AS completed, "
            f"SUM(e.results_published = 1) AS results_published, "
            f"COUNT(*) AS total "
            f"FROM exams e"
        )
        row = cur.fetchone()
    return {k: int(v or 0) for k, v in row.items()}


@router.get("")
def list_exams(user: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            f"SELECT e.*, c.name AS class_name, {EXAM_STATUS} AS status, "
            "(SELECT COUNT(*) FROM exam_papers p WHERE p.exam_id = e.id) AS paper_count "
            "FROM exams e LEFT JOIN classes c ON c.id = e.class_id "
            "ORDER BY e.start_date DESC"
        )
        return cur.fetchall()


@router.post("", status_code=201)
def create_exam(body: ExamCreate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        if body.class_id is not None:
            cur.execute("SELECT id FROM classes WHERE id = %s", (body.class_id,))
            if not cur.fetchone():
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not found")
        cur.execute(
            "INSERT INTO exams (name, class_id, start_date, end_date) VALUES (%s, %s, %s, %s)",
            (body.name, body.class_id, body.start_date, body.end_date),
        )
        return {"id": cur.lastrowid}


@router.get("/{exam_id}")
def exam_detail(exam_id: int, user: dict = Depends(staff)):
    """Exam info + the classes it applies to (each with subjects, paper and
    section counts) + sections for the results flow."""
    with db_cursor() as cur:
        exam = _get_exam(cur, exam_id)

        # classes this exam applies to, with subject/paper/student counts
        class_filter = "" if exam["class_id"] is None else "WHERE c.id = %s"
        params = () if exam["class_id"] is None else (exam["class_id"],)
        cur.execute(
            "SELECT c.id, c.name, "
            "(SELECT COUNT(*) FROM class_subjects cs WHERE cs.class_id = c.id) AS subject_count, "
            "(SELECT COUNT(*) FROM exam_papers p WHERE p.exam_id = %s AND p.class_id = c.id) AS paper_count, "
            "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.class_id = c.id) AS student_count "
            f"FROM classes c {class_filter} ORDER BY c.name",
            (exam_id, *params),
        )
        exam["classes"] = cur.fetchall()

        # sections (for results entry) — all sections of the applicable classes
        section_filter = "" if exam["class_id"] is None else "WHERE s.class_id = %s"
        cur.execute(
            "SELECT s.id, s.name, s.class_id, c.name AS class_name, "
            "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.section_id = s.id) AS student_count "
            f"FROM sections s JOIN classes c ON c.id = s.class_id {section_filter} "
            "ORDER BY c.name, s.name",
            params,
        )
        exam["sections"] = cur.fetchall()
    return exam


@router.put("/{exam_id}")
def update_exam(exam_id: int, body: ExamUpdate, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        e = _get_exam(cur, exam_id)
        data = body.model_dump(exclude_unset=True)
        merged = {**e, **data}
        cur.execute(
            "UPDATE exams SET name=%s, start_date=%s, end_date=%s, results_published=%s "
            "WHERE id=%s",
            (
                merged["name"], merged["start_date"], merged["end_date"],
                int(bool(merged["results_published"])), exam_id,
            ),
        )
    return {"updated": exam_id}


@router.delete("/{exam_id}")
def delete_exam(exam_id: int, actor: dict = Depends(manager)):
    with db_cursor() as cur:
        cur.execute("SELECT paper_path FROM exams WHERE id = %s", (exam_id,))
        e = cur.fetchone()
        if not e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
        cur.execute("SELECT paper_path FROM exam_papers WHERE exam_id = %s", (exam_id,))
        subject_papers = [r["paper_path"] for r in cur.fetchall()]
        cur.execute("DELETE FROM exams WHERE id = %s", (exam_id,))
    delete_file(e["paper_path"])
    for p in subject_papers:
        delete_file(p)
    return {"deleted": exam_id}


# ---------- subject-wise question papers ----------

@router.get("/{exam_id}/classes/{class_id}/papers")
def class_papers(exam_id: int, class_id: int, user: dict = Depends(staff)):
    """The class's subjects, each with its uploaded paper (if any)."""
    with db_cursor() as cur:
        exam = _get_exam(cur, exam_id)
        if not _exam_applies_to_class(exam, class_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not in this exam")
        cur.execute("SELECT id, name FROM classes WHERE id = %s", (class_id,))
        cls = cur.fetchone()
        if not cls:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not found")
        cur.execute(
            "SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id", (class_id,)
        )
        subjects = [r["name"] for r in cur.fetchall()]
        cur.execute(
            "SELECT p.*, u.full_name AS uploaded_by_name "
            "FROM exam_papers p JOIN users u ON u.id = p.uploaded_by "
            "WHERE p.exam_id = %s AND p.class_id = %s",
            (exam_id, class_id),
        )
        papers = {p["subject"]: p for p in cur.fetchall()}
    # keep papers whose subject was later removed from the class visible
    for extra in papers:
        if extra not in subjects:
            subjects.append(extra)
    return {
        "exam": {k: exam[k] for k in ("id", "name", "start_date", "end_date", "status")},
        "class": cls,
        "subjects": [
            {"subject": s, "paper": papers.get(s)} for s in subjects
        ],
    }


@router.post("/{exam_id}/classes/{class_id}/papers", status_code=201)
def upload_subject_paper(
    exam_id: int,
    class_id: int,
    subject: str = Form(...),
    file: UploadFile = File(...),
    actor: dict = Depends(paper_manager),
):
    subject = subject.strip()
    if not subject:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Subject is required")
    with db_cursor() as cur:
        exam = _get_exam(cur, exam_id)
        if not _exam_applies_to_class(exam, class_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not in this exam")
        cur.execute("SELECT id FROM classes WHERE id = %s", (class_id,))
        if not cur.fetchone():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Class not found")
        path = save_upload(file, "papers", PDF_TYPES, max_mb=15)
        cur.execute(
            "SELECT id, paper_path FROM exam_papers "
            "WHERE exam_id = %s AND class_id = %s AND subject = %s",
            (exam_id, class_id, subject),
        )
        old = cur.fetchone()
        if old:  # replace
            delete_file(old["paper_path"])
            cur.execute(
                "UPDATE exam_papers SET paper_path=%s, paper_name=%s, uploaded_by=%s "
                "WHERE id=%s",
                (path, file.filename, actor["id"], old["id"]),
            )
            return {"id": old["id"], "subject": subject, "paper_name": file.filename}
        cur.execute(
            "INSERT INTO exam_papers (exam_id, class_id, subject, paper_path, paper_name, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (exam_id, class_id, subject, path, file.filename, actor["id"]),
        )
        return {"id": cur.lastrowid, "subject": subject, "paper_name": file.filename}


@router.get("/{exam_id}/papers/{paper_id}/file")
def get_subject_paper(exam_id: int, paper_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM exam_papers WHERE id = %s AND exam_id = %s",
            (paper_id, exam_id),
        )
        paper = cur.fetchone()
    if not paper:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    return FileResponse(
        paper["paper_path"],
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=paper["paper_name"] or "question-paper.pdf",
    )


@router.delete("/{exam_id}/papers/{paper_id}")
def delete_subject_paper(exam_id: int, paper_id: int, actor: dict = Depends(paper_manager)):
    with db_cursor() as cur:
        cur.execute(
            "SELECT paper_path FROM exam_papers WHERE id = %s AND exam_id = %s",
            (paper_id, exam_id),
        )
        paper = cur.fetchone()
        if not paper:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
        cur.execute("DELETE FROM exam_papers WHERE id = %s", (paper_id,))
    delete_file(paper["paper_path"])
    return {"deleted": paper_id}


# ---------- legacy single question paper (kept for old exams) ----------

@router.post("/{exam_id}/paper")
def upload_paper(
    exam_id: int, file: UploadFile = File(...), actor: dict = Depends(paper_manager)
):
    with db_cursor() as cur:
        exam = _get_exam(cur, exam_id)
        path = save_upload(file, "papers", PDF_TYPES, max_mb=15)
        delete_file(exam["paper_path"])
        cur.execute(
            "UPDATE exams SET paper_path = %s, paper_name = %s WHERE id = %s",
            (path, file.filename, exam_id),
        )
    return {"paper_name": file.filename}


@router.get("/{exam_id}/paper")
def get_paper(exam_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        exam = _get_exam(cur, exam_id)
    if not exam["paper_path"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No question paper uploaded")
    return FileResponse(
        exam["paper_path"],
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=exam["paper_name"] or "question-paper.pdf",
    )


# ---------- results ----------

@router.get("/{exam_id}/results")
def exam_results(exam_id: int, section_id: int, user: dict = Depends(staff)):
    with db_cursor() as cur:
        exam = _get_exam(cur, exam_id)
        cur.execute("SELECT id, class_id FROM sections WHERE id = %s", (section_id,))
        section = cur.fetchone()
        if not section or not _exam_applies_to_class(exam, section["class_id"]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not in this exam's class")
        ensure_can_view_section(cur, user, section["class_id"], section_id)
        cur.execute(
            "SELECT u.id AS student_id, u.full_name, u.photo_path, sp.roll_no, "
            "r.marks, r.grade, r.remarks "
            "FROM student_profiles sp "
            "JOIN users u ON u.id = sp.user_id "
            "LEFT JOIN exam_results r ON r.student_id = u.id AND r.exam_id = %s "
            "WHERE sp.section_id = %s AND u.is_active = 1 "
            "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name",
            (exam_id, section_id),
        )
        return {"exam": exam, "rows": cur.fetchall()}


@router.put("/{exam_id}/results")
def save_results(exam_id: int, body: ResultsSave, user: dict = Depends(staff)):
    with db_cursor() as cur:
        exam = _get_exam(cur, exam_id)
        cur.execute("SELECT id, class_id FROM sections WHERE id = %s", (body.section_id,))
        section = cur.fetchone()
        if not section or not _exam_applies_to_class(exam, section["class_id"]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not in this exam's class")
        if user["user_type"] == "teacher" and not is_assigned(
            cur, user, section["class_id"], body.section_id
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not assigned to this section")
        for r in body.results:
            if r.marks is None and not r.grade:
                continue
            cur.execute(
                "INSERT INTO exam_results (exam_id, student_id, marks, grade, remarks) "
                "VALUES (%s, %s, %s, %s, %s) AS new "
                "ON DUPLICATE KEY UPDATE marks=new.marks, grade=new.grade, remarks=new.remarks",
                (exam_id, r.student_id, r.marks, r.grade, r.remarks),
            )
    return {"saved": True}
