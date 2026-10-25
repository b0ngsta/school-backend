"""School documents: files the school shares with parents, by kind.

    timetable   exam date-sheets, a changed class timetable, …
    syllabus    a subject's syllabus for a class

A document is aimed at the whole school (no class), one class, or one
section of a class; syllabus documents name a subject. The file is a PDF or
a photo (see services/attachments.py).

Who may upload:
* managers — anything;
* timetable — the CLASS teacher of the section (a section is required);
* syllabus  — a teacher assigned to the class (or to that section).
Changing or removing a document: managers, or the teacher who uploaded it.
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import BadRequest, Forbidden, NotFound
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.services import access, attachments, audit, notify, pagination, storage

KINDS = ("timetable", "syllabus")

_SELECT = (
    "SELECT d.id, d.kind, d.title, d.note, d.class_id, d.section_id, d.subject, d.attachment_path, "
    "d.attachment_name, d.attachment_type, d.created_by, u.full_name AS uploaded_by, d.created_at, d.updated_at, "
    "c.name AS class_name, s.name AS section_name "
    "FROM school_documents d "
    "LEFT JOIN users u ON u.id = d.created_by "
    "LEFT JOIN classes c ON c.id = d.class_id "
    "LEFT JOIN sections s ON s.id = d.section_id "
    "WHERE d.org_id = %s"
)
ORDER = "d.created_at DESC, d.id DESC"


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise BadRequest(f"kind must be one of: {', '.join(KINDS)}")
    return kind


def _target(db: Db, class_id: int | None, section_id: int | None) -> tuple[int | None, int | None]:
    """Validate the class / section (own school; section inside the class)."""
    if section_id is not None:
        sec = db.one("SELECT class_id FROM sections WHERE id = %s AND org_id = %s", (section_id, db.org))
        if not sec:
            raise NotFound("Section not found")
        if class_id is not None and class_id != sec["class_id"]:
            raise NotFound("Section not in that class")
        return sec["class_id"], section_id
    if class_id is not None and not db.one("SELECT id FROM classes WHERE id = %s AND org_id = %s",
                                           (class_id, db.org)):
        raise NotFound("Class not found")
    return class_id, None


def _ensure_can_upload(db: Db, user: CurrentUser, kind: str, class_id: int | None, section_id: int | None) -> None:
    if user.user_type in MANAGERS:
        return
    if user.user_type != "teacher":
        raise Forbidden("Not allowed")
    if kind == "timetable":
        if section_id is None:
            raise BadRequest("Choose the section this timetable is for")
        if not access.is_class_teacher(db, user, class_id, section_id):
            raise Forbidden("Only the class teacher (or admins) can share a timetable for this section")
        return
    if class_id is None:
        raise BadRequest("Choose the class this syllabus is for")
    if not access.is_assigned(db, user, class_id, section_id):
        raise Forbidden("Not assigned to this class/section")


def _get(db: Db, document_id: int, lock: bool = False) -> dict:
    if lock:  # lock only the document row (not the joined user / class)
        db.one("SELECT id FROM school_documents WHERE id = %s AND org_id = %s FOR UPDATE", (document_id, db.org))
    row = db.one(_SELECT + " AND d.id = %s", (db.org, document_id))
    if not row:
        raise NotFound("Document not found")
    return row


def _editable(db: Db, user: CurrentUser, document_id: int) -> dict:
    doc = _get(db, document_id, lock=True)
    if user.user_type not in MANAGERS and doc["created_by"] != user.id:
        raise Forbidden("Only the person who shared this document (or admins) can change it")
    return doc


def _clean(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


# ------------------------------------------------------------------ staff

def list_documents(db: Db, user: CurrentUser, kind: str | None, class_id: int | None, section_id: int | None,
                   page: pagination.Page | None = None) -> pagination.PageResult:
    sql, params = _SELECT, [db.org]
    if kind is not None:
        sql += " AND d.kind = %s"
        params.append(_kind(kind))
    if class_id is not None:
        sql += " AND d.class_id = %s"
        params.append(class_id)
    if section_id is not None:
        sql += " AND d.section_id = %s"
        params.append(section_id)
    if user.user_type not in MANAGERS:  # teachers: school-wide + their classes + their own uploads
        sql += (" AND (d.class_id IS NULL OR d.created_by = %s OR d.class_id IN "
                "(SELECT ta.class_id FROM teacher_assignments ta WHERE ta.org_id = %s AND ta.teacher_id = %s))")
        params += [user.id, db.org, user.id]
    return pagination.fetch(db, sql, params, ORDER, page)


def create(db: Db, user: CurrentUser, kind: str, title: str, note: str | None, class_id: int | None,
           section_id: int | None, subject: str | None, file: UploadFile) -> dict:
    kind = _kind(kind)
    title = _clean(title, 150)
    if not title:
        raise BadRequest("Title is required")
    subject = _clean(subject, 100)
    if kind == "syllabus" and not subject:
        raise BadRequest("Subject is required for a syllabus")
    with db.transaction():
        class_id, section_id = _target(db, class_id, section_id)
        _ensure_can_upload(db, user, kind, class_id, section_id)
        att = attachments.save(db, file, "documents", default_name=kind)
        document_id = db.execute(
            "INSERT INTO school_documents (org_id, kind, title, note, class_id, section_id, subject, "
            "attachment_path, attachment_name, attachment_type, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, kind, title, _clean(note, 4000), class_id, section_id, subject, *att.columns(), user.id),
        ).lastrowid
        audit.record(db, user, f"document.{kind}.create", "school_document", document_id,
                     {"class_id": class_id, "section_id": section_id})
        _notify_students(db, kind, title, class_id, section_id)
    return _get(db, document_id)


def update(db: Db, user: CurrentUser, document_id: int, title: str | None, note: str | None,
           subject: str | None, file: UploadFile | None) -> dict:
    """Change the title / note / subject and optionally replace the file
    (the class and section stay)."""
    with db.transaction():
        doc = _editable(db, user, document_id)
        new_title = _clean(title, 150) if title is not None else doc["title"]
        if not new_title:
            raise BadRequest("Title is required")
        new_subject = _clean(subject, 100) if subject is not None else doc["subject"]
        if doc["kind"] == "syllabus" and not new_subject:
            raise BadRequest("Subject is required for a syllabus")
        new_note = _clean(note, 4000) if note is not None else doc["note"]
        columns = (doc["attachment_path"], doc["attachment_name"], doc["attachment_type"])
        if file is not None:
            columns = attachments.save(db, file, "documents", default_name=doc["kind"]).columns()
            storage.delete_after_commit(db, doc["attachment_path"])
        db.execute(
            "UPDATE school_documents SET title = %s, note = %s, subject = %s, attachment_path = %s, "
            "attachment_name = %s, attachment_type = %s WHERE id = %s",
            (new_title, new_note, new_subject, *columns, document_id),
        )
        audit.record(db, user, f"document.{doc['kind']}.update", "school_document", document_id)
    return _get(db, document_id)


def delete(db: Db, user: CurrentUser, document_id: int) -> None:
    with db.transaction():
        doc = _editable(db, user, document_id)
        db.execute("DELETE FROM school_documents WHERE id = %s", (document_id,))
        storage.delete_after_commit(db, doc["attachment_path"])
        audit.record(db, user, f"document.{doc['kind']}.delete", "school_document", document_id)


def _notify_students(db: Db, kind: str, title: str, class_id: int | None, section_id: int | None) -> None:
    sql = ("SELECT u.id FROM users u JOIN student_profiles sp ON sp.user_id = u.id "
           "WHERE u.org_id = %s AND u.user_type = 'student' AND u.is_active = 1")
    params: list = [db.org]
    if section_id is not None:
        sql += " AND sp.section_id = %s"
        params.append(section_id)
    elif class_id is not None:
        sql += " AND sp.class_id = %s"
        params.append(class_id)
    ids = [r["id"] for r in db.all(sql, params)]
    label = "🗓️ New timetable" if kind == "timetable" else "📘 New syllabus"
    notify.notify(db, ids, kind, f"{label}: {title}", data=notify.route(kind, student=True))


# --------------------------------------------------------------- students

def _student_scope(db: Db, student_id: int) -> tuple[str, list]:
    """WHERE fragment: documents for the whole school, the student's class or section."""
    me = db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s AND org_id = %s",
                (student_id, db.org)) or {}
    return ("(d.class_id IS NULL OR (d.class_id <=> %s AND (d.section_id IS NULL OR d.section_id <=> %s)))",
            [me.get("class_id"), me.get("section_id")])


def for_student(db: Db, student_id: int, kind: str, page: pagination.Page | None = None) -> pagination.PageResult:
    scope, params = _student_scope(db, student_id)
    return pagination.fetch(db, _SELECT + " AND d.kind = %s AND " + scope, [db.org, _kind(kind), *params],
                            ORDER, page)


def syllabus_for_student(db: Db, student_id: int) -> list[dict]:
    """One entry per subject of the student's class (in the class's order),
    each with its syllabus files; subjects named only on a document follow,
    then files without a subject under "General"."""
    me = db.one("SELECT class_id FROM student_profiles WHERE user_id = %s AND org_id = %s",
                (student_id, db.org)) or {}
    subjects = [r["name"] for r in db.all(
        "SELECT cs.name FROM class_subjects cs JOIN classes c ON c.id = cs.class_id "
        "WHERE cs.class_id = %s AND c.org_id = %s ORDER BY cs.id", (me.get("class_id"), db.org))]
    docs = for_student(db, student_id, "syllabus").rows
    groups: dict[str, dict] = {}
    for name in subjects:
        groups.setdefault(name.strip().lower(), {"subject": name, "documents": []})
    for doc in docs:
        name = (doc["subject"] or "").strip() or "General"
        groups.setdefault(name.lower(), {"subject": name, "documents": []})["documents"].append(doc)
    return list(groups.values())
