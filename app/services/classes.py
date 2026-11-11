"""Classes, sections and class subjects."""
from __future__ import annotations

from app.core.errors import BadRequest, Conflict, DuplicateKey, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.classes import ClassCreate, ClassUpdate, SectionCreate, SubjectsUpdate
from app.services import audit, school_profile, storage


def _clean_subjects(subjects: list[str]) -> list[str]:
    """Trim, dedupe (case-insensitive), keep order."""
    seen, out = set(), []
    for s in subjects:
        name = (s or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name[:100])
    return out


def _set_subjects(db: Db, class_id: int, subjects: list[str]) -> None:
    db.execute("DELETE FROM class_subjects WHERE class_id = %s", (class_id,))
    db.execute_many(
        "INSERT INTO class_subjects (class_id, name) VALUES (%s, %s)",
        [(class_id, name) for name in _clean_subjects(subjects)],
    )


WHOLE_CLASS = ""  # sections.name of the one section of a class that has no sections (0021)


def section_names(count: int) -> list[str]:
    """4 → A, B, C, D."""
    return [chr(ord("A") + i) for i in range(count)]


def create_class(db: Db, body: ClassCreate) -> dict:
    """`sections`: N → sections A… are created with the class; 0 → a class
    that is not split into sections: it gets ONE section with an empty name,
    so attendance, class teacher, timetable… work as for any section, and
    `has_sections = 0` tells clients not to show a section; left out → no
    sections yet (as before; add them one by one)."""
    with db.transaction():
        if db.one("SELECT id FROM classes WHERE org_id = %s AND name = %s", (db.org, body.name)):
            raise Conflict("Class already exists")
        whole = body.sections == 0
        try:
            class_id = db.execute(
                "INSERT INTO classes (org_id, name, fee_amount, has_sections) VALUES (%s, %s, %s, %s)",
                (db.org, body.name, body.fee_amount, 0 if whole else 1),
            ).lastrowid
        except DuplicateKey:
            raise Conflict("Class already exists") from None
        if body.subjects:
            _set_subjects(db, class_id, body.subjects)
        names = [WHOLE_CLASS] if whole else section_names(body.sections or 0)
        sections = [{"id": db.execute("INSERT INTO sections (org_id, class_id, name) VALUES (%s, %s, %s)",
                                      (db.org, class_id, name)).lastrowid, "name": name} for name in names]
    return {"id": class_id, "name": body.name, "has_sections": 0 if whole else 1, "sections": sections}


def update_class(db: Db, actor: CurrentUser | None, class_id: int, body: ClassUpdate) -> dict:
    """Rename a class and / or change its fee for the year. The fee can't go
    below what is already billed to the class in parts this session (take a
    part back on the Fees page first)."""
    changes = body.model_dump(exclude_unset=True)
    with db.transaction():
        cls = db.one("SELECT id, name, fee_amount FROM classes WHERE id = %s AND org_id = %s FOR UPDATE",
                     (class_id, db.org))
        if not cls:
            raise NotFound("Class not found")
        new: dict = {}
        name = (changes.get("name") or "").strip()
        if name and name != cls["name"]:
            if db.one("SELECT id FROM classes WHERE org_id = %s AND name = %s AND id <> %s",
                      (db.org, name, class_id)):
                raise Conflict("Class already exists")
            new["name"] = name
        if "fee_amount" in changes:
            fee = changes["fee_amount"]
            fee = round(float(fee), 2) if fee else None
            old = round(float(cls["fee_amount"]), 2) if cls["fee_amount"] else None
            if fee != old:
                session = school_profile.current_session(db)["label"]
                billed = round(float(db.scalar(
                    "SELECT COALESCE(SUM(amount), 0) FROM class_fee_installments "
                    "WHERE org_id = %s AND class_id = %s AND session = %s", (db.org, class_id, session)) or 0), 2)
                if billed > (fee or 0) + 0.004:
                    raise BadRequest(
                        f"₹{billed:,.0f} of {cls['name']}'s fee is already billed for {session}, "
                        "so the fee can't be less than that.",
                        extra={"code": "below_billed", "allotted": billed})
                new["fee_amount"] = fee
        if new:
            try:
                db.execute(f"UPDATE classes SET {', '.join(f'{k} = %s' for k in new)} WHERE id = %s",
                           (*new.values(), class_id))
            except DuplicateKey:
                raise Conflict("Class already exists") from None
            audit.record(db, actor, "class.update", "class", class_id,
                         {"before": {k: cls[k] for k in new}, "after": new})
        row = db.one("SELECT id, name, fee_amount, has_sections FROM classes WHERE id = %s", (class_id,))
    return row


def update_subjects(db: Db, class_id: int, body: SubjectsUpdate) -> dict:
    with db.transaction():
        if not db.one("SELECT id FROM classes WHERE id = %s AND org_id = %s FOR UPDATE", (class_id, db.org)):
            raise NotFound("Class not found")
        _set_subjects(db, class_id, body.subjects)
        rows = db.all("SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id", (class_id,))
    return {"subjects": [r["name"] for r in rows]}


def list_classes(db: Db) -> list[dict]:
    """Classes with sections; each section carries student count + class teacher name."""
    classes = db.all("SELECT * FROM classes WHERE org_id = %s ORDER BY name", (db.org,))
    sections = db.all(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM student_profiles sp WHERE sp.section_id = s.id) AS student_count, "
        "(SELECT u.full_name FROM teacher_assignments ta JOIN users u ON u.id = ta.teacher_id "
        " WHERE ta.section_id = s.id AND ta.role = 'class_teacher' LIMIT 1) AS class_teacher "
        "FROM sections s WHERE s.org_id = %s ORDER BY s.class_id, s.name",
        (db.org,),
    )
    subjects = db.all("SELECT cs.class_id, cs.name FROM class_subjects cs JOIN classes c ON c.id = cs.class_id "
                      "WHERE c.org_id = %s ORDER BY cs.class_id, cs.id", (db.org,))

    by_class: dict[int, list] = {}
    for s in sections:
        by_class.setdefault(s["class_id"], []).append(s)
    subj_by_class: dict[int, list] = {}
    for s in subjects:
        subj_by_class.setdefault(s["class_id"], []).append(s["name"])
    for c in classes:
        c["sections"] = by_class.get(c["id"], [])
        c["subjects"] = subj_by_class.get(c["id"], [])
    return classes


# ------------------------------------------------------------------ deleting
# A class or section can only be deleted while nothing has been recorded in it:
# deleting used to erase attendance, homework, documents, substitutions, lesson
# plans and marks along with it (migration 0017 makes the database refuse that
# too). Current set-up goes with it as before: timetable slots, teacher
# assignments, subjects, exams without any results (and their question papers);
# students in it are left without a section, as before.

_RECORD_WORDS = {
    "attendance": ("attendance record", "attendance records"),
    "homework": ("homework post", "homework posts"),
    "documents": ("timetable / syllabus file", "timetable / syllabus files"),
    "substitutions": ("substitution record", "substitution records"),
    "lesson_plans": ("lesson plan", "lesson plans"),
    "exams_with_results": ("exam with results", "exams with results"),
}


def _in(ids: list[int]) -> str:
    return ",".join(["%s"] * len(ids))


def _section_records(db: Db, section_ids: list[int]) -> dict[str, int]:
    if not section_ids:
        return {}
    ph = _in(section_ids)
    row = db.one(
        f"SELECT (SELECT COUNT(*) FROM attendance WHERE section_id IN ({ph})) AS attendance, "
        f"(SELECT COUNT(*) FROM class_homework WHERE section_id IN ({ph})) AS homework, "
        f"(SELECT COUNT(*) FROM school_documents WHERE section_id IN ({ph})) AS documents, "
        f"(SELECT COUNT(*) FROM substitution_requests WHERE section_id IN ({ph})) AS substitutions",
        (*section_ids, *section_ids, *section_ids, *section_ids),
    ) or {}
    return {k: int(v or 0) for k, v in row.items()}


def _class_records(db: Db, class_id: int, section_ids: list[int]) -> dict[str, int]:
    ph = _in(section_ids) if section_ids else "NULL"
    sec = tuple(section_ids)
    row = db.one(
        f"SELECT (SELECT COUNT(*) FROM attendance WHERE section_id IN ({ph})) AS attendance, "
        "(SELECT COUNT(*) FROM class_homework WHERE class_id = %s) AS homework, "
        f"(SELECT COUNT(*) FROM school_documents WHERE class_id = %s OR section_id IN ({ph})) AS documents, "
        f"(SELECT COUNT(*) FROM substitution_requests WHERE class_id = %s OR section_id IN ({ph})) "
        "AS substitutions, "
        "(SELECT COUNT(*) FROM lesson_plans WHERE class_id = %s) AS lesson_plans, "
        "(SELECT COUNT(DISTINCT r.exam_id) FROM exam_results r JOIN exams e ON e.id = r.exam_id "
        " WHERE e.class_id = %s) AS exams_with_results",
        (*sec, class_id, class_id, *sec, class_id, *sec, class_id, class_id),
    ) or {}
    return {k: int(v or 0) for k, v in row.items()}


def _describe(records: dict[str, int]) -> str:
    parts = [f"{n:,} {_RECORD_WORDS[k][0 if n == 1 else 1]}" for k, n in records.items() if n]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _refuse(what: str, records: dict[str, int], advice: str) -> None:
    held = {k: n for k, n in records.items() if n}
    if held:
        raise Conflict(f"{what} can't be deleted: it holds {_describe(held)}, and deleting it would erase "
                       f"them. {advice}", extra={"code": "has_records", "records": held})


def delete_class(db: Db, actor: CurrentUser | None, class_id: int) -> dict:
    with db.transaction():
        cls = db.one("SELECT id, name FROM classes WHERE id = %s AND org_id = %s FOR UPDATE", (class_id, db.org))
        if not cls:
            raise NotFound("Class not found")
        section_ids = [r["id"] for r in db.all("SELECT id FROM sections WHERE class_id = %s", (class_id,))]
        _refuse(cls["name"], _class_records(db, class_id, section_ids),
                "A class with records stays; move its students to another class if it is no longer used.")
        # exams without results go with the class, as before: remove their question-paper files too
        papers = [r["paper_path"] for r in db.all(
            "SELECT paper_path FROM exams WHERE class_id = %s AND paper_path IS NOT NULL "
            "UNION ALL SELECT p.paper_path FROM exam_papers p WHERE p.class_id = %s AND p.paper_path IS NOT NULL",
            (class_id, class_id))]
        students = db.scalar("SELECT COUNT(*) FROM student_profiles WHERE class_id = %s", (class_id,))
        db.execute("DELETE FROM classes WHERE id = %s", (class_id,))
        for path in papers:
            storage.delete_after_commit(db, path)
        audit.record(db, actor, "class.delete", "class", class_id,
                     {"name": cls["name"], "sections": len(section_ids), "students_unassigned": int(students or 0)})
    return {"deleted": class_id}


def create_section(db: Db, class_id: int, body: SectionCreate) -> dict:
    with db.transaction():
        cls = db.one("SELECT id, has_sections FROM classes WHERE id = %s AND org_id = %s FOR UPDATE",
                     (class_id, db.org))
        if not cls:
            raise NotFound("Class not found")
        if not cls["has_sections"]:
            # the class was one group so far: that group (students, class teacher, attendance…)
            # becomes the first named section; more sections can then be added as usual
            whole = db.one("SELECT id FROM sections WHERE class_id = %s AND name = %s FOR UPDATE",
                           (class_id, WHOLE_CLASS))
            db.execute("UPDATE classes SET has_sections = 1 WHERE id = %s", (class_id,))
            if whole:
                db.execute("UPDATE sections SET name = %s WHERE id = %s", (body.name, whole["id"]))
                return {"id": whole["id"], "class_id": class_id, "name": body.name, "renamed": True}
        if db.one("SELECT id FROM sections WHERE class_id = %s AND name = %s", (class_id, body.name)):
            raise Conflict("Section already exists")
        try:
            section_id = db.execute(
                "INSERT INTO sections (org_id, class_id, name) VALUES (%s, %s, %s)", (db.org, class_id, body.name)
            ).lastrowid
        except DuplicateKey:
            raise Conflict("Section already exists") from None
    return {"id": section_id, "class_id": class_id, "name": body.name}


def delete_section(db: Db, actor: CurrentUser | None, class_id: int, section_id: int) -> dict:
    with db.transaction():
        section = db.one(
            "SELECT s.id, s.name, c.name AS class_name FROM sections s JOIN classes c ON c.id = s.class_id "
            "WHERE s.id = %s AND s.class_id = %s AND s.org_id = %s FOR UPDATE",
            (section_id, class_id, db.org),
        )
        if not section:
            raise NotFound("Section not found")
        if section["name"] == WHOLE_CLASS:
            raise BadRequest(f"{section['class_name']} has no sections — delete the class itself instead.")
        _refuse(f"Section {section['name']}", _section_records(db, [section_id]),
                "A section with records stays; move its students to another section if it is no longer used.")
        students = db.scalar("SELECT COUNT(*) FROM student_profiles WHERE section_id = %s", (section_id,))
        db.execute("DELETE FROM sections WHERE id = %s", (section_id,))
        audit.record(db, actor, "section.delete", "section", section_id,
                     {"class_id": class_id, "class": section["class_name"], "name": section["name"],
                      "students_unassigned": int(students or 0)})
    return {"deleted": section_id}
