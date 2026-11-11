"""Exam format (migration 0021): the exams that make up a class's final
result for the session, each with the marks it carries there —

    Notebook 5 + Handwriting 5 + Term 1 20 + Final exam 70 = 100.

Every part IS an exam (table exams): its results are entered / uploaded and
published on their own, per subject, exactly like any exam. The final result
of a subject is the sum of its marks in the parts (services/final_results.py).

    GET  /exam-format?class_id=      staff
    PUT  /exam-format                managers: {class_id, parts: [{exam_id?, name, max_marks, start_date?, end_date?}]}
    POST /exam-format/copy           managers: {from_class_id, to_class_ids}

Saving replaces the class's list: a part without `exam_id` creates an exam for
that class (dates default to the session's last day — "not scheduled yet");
a part with `exam_id` uses an existing exam of the class (or an all-classes
exam). A part taken off the list only removes the exam itself when this screen
created it and it has no marks or question papers; otherwise the exam stays in
Exams and simply no longer counts.
"""
from __future__ import annotations

from app.core.errors import BadRequest, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.exams import FormatCopy, FormatSave
from app.services import audit, school_profile, storage
from app.services.exams import EXAM_STATUS


def _class(db: Db, class_id: int, *, lock: bool = False) -> dict:
    cls = db.one("SELECT id, name FROM classes WHERE id = %s AND org_id = %s" + (" FOR UPDATE" if lock else ""),
                 (class_id, db.org))
    if not cls:
        raise NotFound("Class not found")
    return cls


def parts(db: Db, class_id: int, session: str | None = None) -> list[dict]:
    """The class's parts for the session, in order."""
    session = session or school_profile.current_session(db)["label"]
    rows = db.all(
        f"SELECT p.id AS part_id, p.exam_id, p.max_marks, p.position, p.created_exam, e.name, e.class_id, "
        f"e.start_date, e.end_date, e.results_published, e.max_marks AS exam_max_marks, {EXAM_STATUS} AS status, "
        "(SELECT COUNT(*) FROM exam_results r JOIN student_profiles sp ON sp.user_id = r.student_id "
        " WHERE r.exam_id = p.exam_id AND sp.class_id = p.class_id) AS marks_count "
        "FROM exam_format_parts p JOIN exams e ON e.id = p.exam_id "
        "WHERE p.org_id = %s AND p.class_id = %s AND p.session = %s ORDER BY p.position, p.id",
        (db.org, class_id, session))
    for r in rows:
        r["max_marks"] = float(r["max_marks"])
        r["exam_max_marks"] = float(r["exam_max_marks"]) if r["exam_max_marks"] is not None else None
        r["all_classes"] = r.pop("class_id") is None
        r["results_published"] = bool(r["results_published"])
        r["has_marks"] = bool(r.pop("marks_count"))
        r["created_exam"] = bool(r["created_exam"])
    return rows


def get(db: Db, class_id: int) -> dict:
    cls = _class(db, class_id)
    sess = school_profile.current_session(db)
    items = parts(db, class_id, sess["label"])
    used = [p["exam_id"] for p in items]
    not_in = f" AND e.id NOT IN ({','.join(['%s'] * len(used))})" if used else ""
    others = db.all(
        f"SELECT e.id, e.name, e.start_date, e.end_date, e.max_marks, e.results_published, {EXAM_STATUS} AS status, "
        "e.class_id IS NULL AS all_classes FROM exams e "
        "WHERE e.org_id = %s AND (e.class_id = %s OR e.class_id IS NULL) AND e.start_date BETWEEN %s AND %s"
        + not_in + " ORDER BY e.start_date, e.id",
        (db.org, class_id, sess["start"], sess["end"], *used))
    for o in others:
        o["all_classes"], o["results_published"] = bool(o["all_classes"]), bool(o["results_published"])
        o["max_marks"] = float(o["max_marks"]) if o["max_marks"] is not None else None
    published = int(db.scalar("SELECT COUNT(*) FROM final_results WHERE org_id = %s AND class_id = %s AND session = %s",
                              (db.org, class_id, sess["label"])) or 0)
    return {
        "class": cls,
        "session": {"label": sess["label"], "start": sess["start"], "end": sess["end"]},
        "parts": items,
        "total": round(sum(p["max_marks"] for p in items), 2),
        "other_exams": others,  # exams of this class and session that are not part of the final result
        "final_published_sections": published,
    }


def _remove_part(db: Db, part: dict) -> bool:
    """Take a part off the list. Returns True when its exam was removed too
    (made by this screen, and nothing was ever recorded in it)."""
    db.execute("DELETE FROM exam_format_parts WHERE id = %s", (part["part_id"],))
    if not part["created_exam"] or part["all_classes"]:
        return False
    if db.scalar("SELECT COUNT(*) FROM exam_format_parts WHERE exam_id = %s", (part["exam_id"],)):
        return False
    if db.scalar("SELECT COUNT(*) FROM exam_results WHERE exam_id = %s", (part["exam_id"],)):
        return False
    exam = db.one("SELECT paper_path FROM exams WHERE id = %s AND org_id = %s FOR UPDATE", (part["exam_id"], db.org))
    if not exam or exam["paper_path"]:
        return False
    if db.scalar("SELECT COUNT(*) FROM exam_papers WHERE exam_id = %s", (part["exam_id"],)):
        return False
    db.execute("DELETE FROM exams WHERE id = %s", (part["exam_id"],))
    storage.delete_after_commit(db, exam["paper_path"])
    return True


def save(db: Db, actor: CurrentUser, body: FormatSave) -> dict:
    sess = school_profile.current_session(db)
    with db.transaction():
        cls = _class(db, body.class_id, lock=True)
        current = {p["exam_id"]: p for p in parts(db, body.class_id, sess["label"])}
        wanted_ids = [p.exam_id for p in body.parts if p.exam_id is not None]
        if len(wanted_ids) != len(set(wanted_ids)):
            raise BadRequest("The same exam is on the list twice")
        names: set[str] = set()
        created = removed = 0
        keep: set[int] = set()
        for position, part in enumerate(body.parts):
            name = (part.name or "").strip()
            if part.exam_id is not None:
                exam = db.one("SELECT id, name, class_id, start_date, end_date, max_marks FROM exams "
                              "WHERE id = %s AND org_id = %s AND (class_id = %s OR class_id IS NULL) FOR UPDATE",
                              (part.exam_id, db.org, body.class_id))
                if not exam:
                    raise NotFound("Exam not found for this class")
                own = exam["class_id"] is not None
                if own:
                    start = part.start_date or exam["start_date"]
                    end = part.end_date or exam["end_date"]
                    if end < start:
                        raise BadRequest(f"{name or exam['name']}: the end date is before the start date")
                    has_marks = current.get(exam["id"], {}).get("has_marks") or bool(db.scalar(
                        "SELECT COUNT(*) FROM exam_results WHERE exam_id = %s", (exam["id"],)))
                    # marks already entered keep the maximum they were entered against
                    new_max = exam["max_marks"] if has_marks and exam["max_marks"] is not None else part.max_marks
                    db.execute("UPDATE exams SET name = %s, start_date = %s, end_date = %s, max_marks = %s "
                               "WHERE id = %s", (name or exam["name"], start, end, new_max, exam["id"]))
                label = (name if own and name else exam["name"]).lower()
                exam_id, made = exam["id"], current.get(exam["id"], {}).get("created_exam", False)
            else:
                start = part.start_date or part.end_date or sess["end"]
                end = part.end_date or start
                exam_id = db.execute(
                    "INSERT INTO exams (org_id, name, class_id, start_date, end_date, max_marks) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (db.org, name, body.class_id, start, end, part.max_marks)).lastrowid
                label, made = name.lower(), True
                created += 1
            if label in names:
                raise BadRequest(f"Two exams on the list are called '{name or label}'")
            names.add(label)
            keep.add(exam_id)
            db.execute(
                "INSERT INTO exam_format_parts (org_id, class_id, session, exam_id, max_marks, position, created_exam) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE max_marks = VALUES(max_marks), position = VALUES(position), "
                "session = VALUES(session)",
                (db.org, body.class_id, sess["label"], exam_id, part.max_marks, position, int(bool(made))))
        for exam_id, part in current.items():
            if exam_id not in keep:
                removed += 1
                _remove_part(db, part)
        audit.record(db, actor, "exam_format.save", "class", body.class_id,
                     {"class": cls["name"], "session": sess["label"], "created": created, "removed": removed,
                      "parts": [{"exam_id": p.exam_id, "name": p.name, "max_marks": p.max_marks}
                                for p in body.parts]})
    return get(db, body.class_id)


def copy(db: Db, actor: CurrentUser, body: FormatCopy) -> dict:
    """Give other classes the same list of exams. A class that already has a
    format is left alone (reported in `skipped`)."""
    sess = school_profile.current_session(db)
    copied, skipped = [], []
    with db.transaction():
        _class(db, body.from_class_id)
        source = parts(db, body.from_class_id, sess["label"])
        if not source:
            raise BadRequest("That class has no exam format to copy yet")
        for class_id in dict.fromkeys(body.to_class_ids):
            if class_id == body.from_class_id:
                continue
            cls = _class(db, class_id, lock=True)
            if parts(db, class_id, sess["label"]):
                skipped.append(cls["name"])
                continue
            for position, p in enumerate(source):
                if p["all_classes"]:
                    exam_id, made = p["exam_id"], False
                else:
                    exam_id = db.execute(
                        "INSERT INTO exams (org_id, name, class_id, start_date, end_date, max_marks) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (db.org, p["name"], class_id, p["start_date"], p["end_date"], p["max_marks"])).lastrowid
                    made = True
                db.execute(
                    "INSERT INTO exam_format_parts (org_id, class_id, session, exam_id, max_marks, position, "
                    "created_exam) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (db.org, class_id, sess["label"], exam_id, p["max_marks"], position, int(made)))
            copied.append(cls["name"])
        audit.record(db, actor, "exam_format.copy", "class", body.from_class_id,
                     {"session": sess["label"], "copied_to": copied, "skipped": skipped})
    return {"copied": copied, "skipped": skipped}
