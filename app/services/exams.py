"""Exams — schedule, subject-wise question papers, per-student results.

- staff can view exams, sections and results
- results entry: managers + teachers assigned to the section
- question paper upload: admin / sub_admin / coordinator / principal
- exams with class_id = NULL apply to ALL classes
- papers are per exam + class + subject (see exam_papers table)
"""
from __future__ import annotations

from fastapi import UploadFile
from fastapi.responses import FileResponse

from app.core.errors import BadRequest, Forbidden, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.exams import ExamCreate, ExamUpdate, ResultsSave
from app.services import access, audit, storage

EXAM_STATUS = (
    "CASE WHEN CURDATE() < e.start_date THEN 'upcoming' "
    "WHEN CURDATE() > e.end_date THEN 'completed' ELSE 'ongoing' END"
)


def get_exam(db: Db, exam_id: int, *, lock: bool = False) -> dict:
    exam = db.one(
        f"SELECT e.*, c.name AS class_name, {EXAM_STATUS} AS status "
        "FROM exams e LEFT JOIN classes c ON c.id = e.class_id WHERE e.id = %s AND e.org_id = %s"
        + (" FOR UPDATE" if lock else ""),
        (exam_id, db.org),
    )
    if not exam:
        raise NotFound("Exam not found")
    return exam


def _applies_to_class(exam: dict, class_id: int) -> bool:
    return exam["class_id"] is None or exam["class_id"] == class_id


def stats(db: Db) -> dict:
    row = db.one(
        f"SELECT "
        f"SUM({EXAM_STATUS} = 'upcoming') AS upcoming, "
        f"SUM({EXAM_STATUS} = 'ongoing') AS ongoing, "
        f"SUM({EXAM_STATUS} = 'completed') AS completed, "
        f"SUM(e.results_published = 1) AS results_published, "
        f"COUNT(*) AS total "
        f"FROM exams e WHERE e.org_id = %s",
        (db.org,),
    )
    return {k: int(v or 0) for k, v in row.items()}


def list_exams(db: Db) -> list[dict]:
    return db.all(
        f"SELECT e.*, c.name AS class_name, {EXAM_STATUS} AS status, "
        "(SELECT COUNT(*) FROM exam_papers p WHERE p.exam_id = e.id) AS paper_count "
        "FROM exams e LEFT JOIN classes c ON c.id = e.class_id "
        "WHERE e.org_id = %s ORDER BY e.start_date DESC",
        (db.org,),
    )


def create_exam(db: Db, body: ExamCreate) -> dict:
    if body.class_id is not None and not db.one("SELECT id FROM classes WHERE id = %s AND org_id = %s",
                                                (body.class_id, db.org)):
        raise NotFound("Class not found")
    exam_id = db.execute(
        "INSERT INTO exams (org_id, name, class_id, start_date, end_date, max_marks) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (db.org, body.name, body.class_id, body.start_date, body.end_date, body.max_marks),
    ).lastrowid
    return {"id": exam_id}


def exam_detail(db: Db, exam_id: int) -> dict:
    """Exam info + the classes it applies to (each with subjects, paper and
    section counts) + sections for the results flow."""
    exam = get_exam(db, exam_id)
    class_filter = "WHERE c.org_id = %s" + ("" if exam["class_id"] is None else " AND c.id = %s")
    params = (db.org,) if exam["class_id"] is None else (db.org, exam["class_id"])
    exam["classes"] = db.all(
        "SELECT c.id, c.name, "
        "(SELECT COUNT(*) FROM class_subjects cs WHERE cs.class_id = c.id) AS subject_count, "
        "(SELECT COUNT(*) FROM exam_papers p WHERE p.exam_id = %s AND p.class_id = c.id) AS paper_count, "
        "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.class_id = c.id) AS student_count "
        f"FROM classes c {class_filter} ORDER BY c.name",
        (exam_id, *params),
    )
    section_filter = "WHERE s.org_id = %s" + ("" if exam["class_id"] is None else " AND s.class_id = %s")
    exam["sections"] = db.all(
        "SELECT s.id, s.name, s.class_id, c.name AS class_name, "
        "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.section_id = s.id) AS student_count "
        f"FROM sections s JOIN classes c ON c.id = s.class_id {section_filter} "
        "ORDER BY c.name, s.name",
        params,
    )
    return exam


def update_exam(db: Db, actor: CurrentUser, exam_id: int, body: ExamUpdate) -> dict:
    with db.transaction():
        current = get_exam(db, exam_id, lock=True)
        changes = body.model_dump(exclude_unset=True)
        merged = {**current, **changes}
        if merged["end_date"] < merged["start_date"]:
            raise BadRequest("end_date must be on/after start_date")
        db.execute(
            "UPDATE exams SET name=%s, start_date=%s, end_date=%s, results_published=%s, max_marks=%s WHERE id=%s",
            (merged["name"], merged["start_date"], merged["end_date"],
             int(bool(merged["results_published"])), merged.get("max_marks"), exam_id),
        )
        if "results_published" in changes and bool(changes["results_published"]) != bool(current["results_published"]):
            audit.record(db, actor, "exam.results_published" if changes["results_published"]
                         else "exam.results_unpublished", "exam", exam_id)
    return {"updated": exam_id}


def delete_exam(db: Db, actor: CurrentUser, exam_id: int) -> dict:
    with db.transaction():
        exam = db.one("SELECT paper_path FROM exams WHERE id = %s AND org_id = %s FOR UPDATE", (exam_id, db.org))
        if not exam:
            raise NotFound("Exam not found")
        subject_papers = [r["paper_path"] for r in
                          db.all("SELECT paper_path FROM exam_papers WHERE exam_id = %s", (exam_id,))]
        db.execute("DELETE FROM exams WHERE id = %s", (exam_id,))
        for path in [exam["paper_path"], *subject_papers]:
            storage.delete_after_commit(db, path)
        audit.record(db, actor, "exam.delete", "exam", exam_id)
    return {"deleted": exam_id}


# ---------- subject-wise question papers ----------

def class_papers(db: Db, exam_id: int, class_id: int) -> dict:
    """The class's subjects, each with its uploaded paper (if any)."""
    exam = get_exam(db, exam_id)
    if not _applies_to_class(exam, class_id):
        raise NotFound("Class not in this exam")
    cls = db.one("SELECT id, name FROM classes WHERE id = %s AND org_id = %s", (class_id, db.org))
    if not cls:
        raise NotFound("Class not found")
    subjects = [r["name"] for r in
                db.all("SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id", (class_id,))]
    papers = {
        p["subject"]: p
        for p in db.all(
            "SELECT p.*, u.full_name AS uploaded_by_name "
            "FROM exam_papers p LEFT JOIN users u ON u.id = p.uploaded_by "
            "WHERE p.exam_id = %s AND p.class_id = %s",
            (exam_id, class_id),
        )
    }
    # keep papers whose subject was later removed from the class visible
    for extra in papers:
        if extra not in subjects:
            subjects.append(extra)
    return {
        "exam": {k: exam[k] for k in ("id", "name", "start_date", "end_date", "status")},
        "class": cls,
        "subjects": [{"subject": s, "paper": papers.get(s)} for s in subjects],
    }


def upload_subject_paper(db: Db, actor: CurrentUser, exam_id: int, class_id: int,
                         subject: str, file: UploadFile) -> dict:
    subject = subject.strip()
    if not subject:
        raise BadRequest("Subject is required")
    with db.transaction():
        exam = get_exam(db, exam_id)
        if not _applies_to_class(exam, class_id):
            raise NotFound("Class not in this exam")
        if not db.one("SELECT id FROM classes WHERE id = %s AND org_id = %s", (class_id, db.org)):
            raise NotFound("Class not found")
        stored = storage.save_upload(db, file, "papers", storage.PDF_TYPES, max_mb=15)
        old = db.one(
            "SELECT id, paper_path FROM exam_papers "
            "WHERE exam_id = %s AND class_id = %s AND subject = %s FOR UPDATE",
            (exam_id, class_id, subject),
        )
        if old:  # replace
            db.execute(
                "UPDATE exam_papers SET paper_path=%s, paper_name=%s, uploaded_by=%s WHERE id=%s",
                (stored.path, stored.original_name or "question-paper.pdf", actor.id, old["id"]),
            )
            storage.delete_after_commit(db, old["paper_path"])
            paper_id = old["id"]
        else:
            paper_id = db.execute(
                "INSERT INTO exam_papers (exam_id, class_id, subject, paper_path, paper_name, uploaded_by) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (exam_id, class_id, subject, stored.path,
                 stored.original_name or "question-paper.pdf", actor.id),
            ).lastrowid
        audit.record(db, actor, "exam_paper.upload", "exam_paper", paper_id,
                     {"exam_id": exam_id, "class_id": class_id, "subject": subject})
    return {"id": paper_id, "subject": subject, "paper_name": file.filename}


def subject_paper_file(db: Db, exam_id: int, paper_id: int) -> FileResponse:
    paper = db.one("SELECT p.* FROM exam_papers p JOIN exams e ON e.id = p.exam_id "
                   "WHERE p.id = %s AND p.exam_id = %s AND e.org_id = %s", (paper_id, exam_id, db.org))
    if not paper:
        raise NotFound("Paper not found")
    return storage.file_response(paper["paper_path"], media_type="application/pdf",
                                 filename=paper["paper_name"] or "question-paper.pdf")


def delete_subject_paper(db: Db, actor: CurrentUser, exam_id: int, paper_id: int) -> dict:
    with db.transaction():
        paper = db.one("SELECT p.paper_path FROM exam_papers p JOIN exams e ON e.id = p.exam_id "
                       "WHERE p.id = %s AND p.exam_id = %s AND e.org_id = %s FOR UPDATE",
                       (paper_id, exam_id, db.org))
        if not paper:
            raise NotFound("Paper not found")
        db.execute("DELETE FROM exam_papers WHERE id = %s", (paper_id,))
        storage.delete_after_commit(db, paper["paper_path"])
        audit.record(db, actor, "exam_paper.delete", "exam_paper", paper_id, {"exam_id": exam_id})
    return {"deleted": paper_id}


# ---------- legacy single question paper (kept for old exams) ----------

def upload_legacy_paper(db: Db, actor: CurrentUser, exam_id: int, file: UploadFile) -> dict:
    with db.transaction():
        exam = get_exam(db, exam_id, lock=True)
        stored = storage.save_upload(db, file, "papers", storage.PDF_TYPES, max_mb=15)
        db.execute("UPDATE exams SET paper_path = %s, paper_name = %s WHERE id = %s",
                   (stored.path, stored.original_name, exam_id))
        storage.delete_after_commit(db, exam["paper_path"])
        audit.record(db, actor, "exam_paper.upload", "exam", exam_id, {"legacy": True})
    return {"paper_name": file.filename}


def legacy_paper_file(db: Db, exam_id: int) -> FileResponse:
    exam = get_exam(db, exam_id)
    if not exam["paper_path"]:
        raise NotFound("No question paper uploaded")
    return storage.file_response(exam["paper_path"], media_type="application/pdf",
                                 filename=exam["paper_name"] or "question-paper.pdf")


# ---------- results ----------

def _section_in_exam(db: Db, exam: dict, section_id: int) -> dict:
    section = db.one("SELECT id, class_id FROM sections WHERE id = %s AND org_id = %s", (section_id, db.org))
    if not section or not _applies_to_class(exam, section["class_id"]):
        raise NotFound("Section not in this exam's class")
    return section


WHOLE_EXAM = ""  # exam_results.subject for a mark of the whole exam (the only kind before 0015)


def _subjects(db: Db, exam_id: int, class_id: int) -> list[str]:
    """The subjects marks can be entered for: the class's subjects, plus any
    subject already used for this exam and class (papers or marks), in case it
    was removed from the class later."""
    names = [r["name"] for r in
             db.all("SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id", (class_id,))]
    used = db.all(
        "SELECT p.subject FROM exam_papers p WHERE p.exam_id = %s AND p.class_id = %s "
        "UNION SELECT r.subject FROM exam_results r JOIN student_profiles sp ON sp.user_id = r.student_id "
        "WHERE r.exam_id = %s AND r.subject <> '' AND sp.class_id = %s",
        (exam_id, class_id, exam_id, class_id),
    )
    for r in used:
        if r["subject"] and r["subject"] not in names:
            names.append(r["subject"])
    return names


def _subject(db: Db, exam_id: int, class_id: int, subject: str | None) -> str:
    name = (subject or "").strip()
    if name and name not in _subjects(db, exam_id, class_id):
        raise BadRequest(f"'{name}' is not a subject of this class")
    return name


def _number(value) -> float | None:
    return float(value) if value is not None else None


def exam_results(db: Db, user: CurrentUser, exam_id: int, section_id: int, subject: str | None = None) -> dict:
    """The section's students with their marks for the whole exam (no subject,
    as before) or for one subject. `subjects` lists what marks can be entered
    for; `max_marks` is the maximum in effect for this subject."""
    exam = get_exam(db, exam_id)
    section = _section_in_exam(db, exam, section_id)
    access.ensure_can_view_section(db, user, section["class_id"], section_id)
    name = _subject(db, exam_id, section["class_id"], subject)
    rows = db.all(
        "SELECT u.id AS student_id, u.full_name, u.photo_path, sp.roll_no, "
        "r.marks, r.grade, r.remarks, r.max_marks "
        "FROM student_profiles sp "
        "JOIN users u ON u.id = sp.user_id "
        "LEFT JOIN exam_results r ON r.student_id = u.id AND r.exam_id = %s AND r.subject = %s "
        "WHERE sp.section_id = %s AND u.is_active = 1 "
        "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name",
        (exam_id, name, section_id),
    )
    saved_max = next((r["max_marks"] for r in rows if r["max_marks"] is not None), None)
    return {
        "exam": exam,
        "rows": rows,
        "subject": name,
        "max_marks": _number(saved_max if saved_max is not None else exam.get("max_marks")),
        "subjects": _subjects(db, exam_id, section["class_id"]),
    }


def save_results(db: Db, user: CurrentUser, exam_id: int, body: ResultsSave) -> dict:
    with db.transaction():
        exam = get_exam(db, exam_id)
        section = _section_in_exam(db, exam, body.section_id)
        if user.user_type == "teacher" and not access.is_assigned(db, user, section["class_id"], body.section_id):
            raise Forbidden("Not assigned to this section")
        subject = _subject(db, exam_id, section["class_id"], body.subject)
        max_marks = body.max_marks if body.max_marks is not None else _number(exam.get("max_marks"))
        entries = [r for r in body.results if r.marks is not None or r.grade]
        access.ensure_students_in_section(db, body.section_id, (r.student_id for r in entries))
        too_high = [r for r in entries if max_marks is not None and r.marks is not None and r.marks > max_marks]
        if too_high:
            names = {row["id"]: row["full_name"] for row in db.all(
                f"SELECT id, full_name FROM users WHERE org_id = %s AND id IN ({','.join(['%s'] * len(too_high))})",
                (db.org, *(r.student_id for r in too_high)))}
            who = ", ".join(names.get(r.student_id, str(r.student_id)) for r in too_high[:3])
            more = f" and {len(too_high) - 3} more" if len(too_high) > 3 else ""
            raise BadRequest(f"Marks can't be more than the maximum ({max_marks:g}): {who}{more}")
        db.execute_many(
            "INSERT INTO exam_results (exam_id, student_id, subject, marks, max_marks, grade, remarks) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE marks = VALUES(marks), max_marks = VALUES(max_marks), "
            "grade = VALUES(grade), remarks = VALUES(remarks)",
            [(exam_id, r.student_id, subject, r.marks, max_marks, r.grade, r.remarks) for r in entries],
        )
        audit.record(db, user, "exam_results.save", "exam", exam_id,
                     {"section_id": body.section_id, "subject": subject or None, "max_marks": max_marks,
                      "results": [{"student_id": r.student_id, "marks": r.marks, "grade": r.grade}
                                  for r in entries]})
    return {"saved": True}
