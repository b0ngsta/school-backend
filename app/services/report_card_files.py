"""Report-card PDFs released to parents (migration 0028).

Nothing is rendered on the server (it has little memory). The office prints
the class's report cards in its own browser exactly as for paper, but picks
"Save as PDF" in the print window, and uploads that ONE file here:

    student 1 front, student 1 back, student 2 front, student 2 back, …

The server checks the page count (students × sides) and, when given, the page
size, then cuts the file into one PDF per student with pikepdf (light: it
copies pages, it does not draw them) and stores each like any other upload.
Parents download their child's file through a signed link, straight from the
storage, so 100 parents at once cost the API almost nothing.

`result_key` = "exam:<exam id>" or "final:<session>": releasing the same
result again replaces that student's file (the old one is removed after the
commit) and the parent is told it was updated.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import tempfile

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import BadRequest, NotFound, PayloadTooLarge, UnsupportedMediaType
from app.db import Db
from app.models.auth import CurrentUser
from app.services import audit, compress, notify, school_profile, storage
from app.services.labels import class_section

log = logging.getLogger(__name__)

MAX_MB = 40                 # one class saved as one PDF (Chrome: ~100–250 KB per student)
MAX_STUDENTS = 400
SUBDIR = "report-cards"
PAGE_TOLERANCE_MM = 4.0     # Chrome rounds page sizes a little
_PT_PER_MM = 72 / 25.4

_SELECT = (
    "SELECT f.id, f.student_id, u.full_name AS student_name, sp.roll_no, f.result_key, f.source, f.exam_id, "
    "e.name AS exam_name, f.session, f.class_id, c.name AS class_name, f.section_id, s.name AS section_name, "
    "f.title, f.file_path, f.file_size, f.pages, f.front_template_id, f.back_template_id, f.released_at, "
    "rb.full_name AS released_by_name "
    "FROM report_card_files f "
    "JOIN users u ON u.id = f.student_id "
    "LEFT JOIN student_profiles sp ON sp.user_id = f.student_id "
    "LEFT JOIN exams e ON e.id = f.exam_id "
    "LEFT JOIN classes c ON c.id = f.class_id "
    "LEFT JOIN sections s ON s.id = f.section_id "
    "LEFT JOIN users rb ON rb.id = f.released_by "
    "WHERE f.org_id = %s"
)


# ------------------------------------------------------------------ helpers

def _safe_name(text: str) -> str:
    text = re.sub(r"[^\w\- .()]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", text)[:120] or "report card"


def _out(row: dict) -> dict:
    """API view: the stored file goes out as a signed `attachment_path` (like every record file)."""
    out = {k: v for k, v in row.items() if k != "file_path"}
    out["attachment_path"] = row["file_path"]
    out["attachment_type"] = "pdf"
    out["attachment_name"] = f"Report card - {_safe_name(row.get('student_name') or '')} - " \
                             f"{_safe_name(row['title'])}.pdf"
    out["label"] = class_section(row.get("class_name"), row.get("section_name")) if row.get("class_name") else None
    return out


def _parse_students(raw: str) -> list[int]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = [p for p in str(raw).split(",") if p.strip()]
    try:
        ids = [int(v) for v in value]
    except (TypeError, ValueError):
        raise BadRequest("students must be a list of student ids") from None
    if not ids:
        raise BadRequest("No students to release")
    if len(ids) > MAX_STUDENTS:
        raise BadRequest(f"Release at most {MAX_STUDENTS} students at a time")
    if len(set(ids)) != len(ids):
        raise BadRequest("A student is listed twice")
    return ids


def _spool(file: UploadFile) -> str:
    """Copy the upload to a temporary file (pikepdf then reads it from disk, not memory)."""
    limit = MAX_MB * 1024 * 1024
    fd, path = tempfile.mkstemp(suffix=".pdf")
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise PayloadTooLarge(f"The PDF is bigger than {MAX_MB} MB. Release one section at a time.")
                out.write(chunk)
        with open(path, "rb") as f:
            if b"%PDF-" not in f.read(1024):
                raise UnsupportedMediaType("Upload the PDF you saved from the print window")
        return path
    except Exception:
        os.unlink(path)
        raise


def _page_size_mm(page) -> tuple[float, float]:
    box = [float(v) for v in page.mediabox]
    w, h = abs(box[2] - box[0]) / _PT_PER_MM, abs(box[3] - box[1]) / _PT_PER_MM
    if int(page.obj.get("/Rotate", 0)) % 180:
        w, h = h, w
    return w, h


def _check_pages(pdf, expected: int, students: int, sides: int,
                 page_w: float | None, page_h: float | None) -> None:
    count = len(pdf.pages)
    if count != expected:
        side_text = "2 sides" if sides == 2 else "1 side"
        raise BadRequest(
            f"The PDF has {count} pages, but {students} students × {side_text} = {expected} pages were expected. "
            "Save the PDF again from the print window without changing its settings "
            "(all pages, 1 page per sheet) and upload that file.")
    if page_w and page_h:
        for number, page in enumerate(pdf.pages, start=1):
            w, h = _page_size_mm(page)
            if abs(w - page_w) > PAGE_TOLERANCE_MM or abs(h - page_h) > PAGE_TOLERANCE_MM:
                raise BadRequest(
                    f"Page {number} is {w:.0f} × {h:.0f} mm, but the design is {page_w:.0f} × {page_h:.0f} mm. "
                    "In the print window choose the same paper size as the design, margins \"Default\", "
                    "scale 100 %, then save the PDF again.")


def _one_student_pdf(src, first: int, sides: int, title: str) -> bytes:
    import pikepdf

    out = pikepdf.new()
    try:
        out.pages.extend(src.pages[first:first + sides])
        out.docinfo["/Title"] = title
        buf = io.BytesIO()
        out.save(buf, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
        return buf.getvalue()
    finally:
        out.close()


# ------------------------------------------------------------------ release

def release(db: Db, actor: CurrentUser, *, source: str, exam_id: int | None, title: str, students_raw: str,
            sides: int, file: UploadFile, front_template_id: int | None = None,
            back_template_id: int | None = None, page_w_mm: float | None = None,
            page_h_mm: float | None = None, send_notice: bool = True) -> dict:
    try:
        import pikepdf
    except ImportError:  # pragma: no cover - pikepdf is in requirements.txt
        raise BadRequest("The server cannot read PDFs yet (pikepdf missing). Ask for the latest server update.") \
            from None
    if source not in ("exam", "final"):
        raise BadRequest("source must be 'exam' or 'final'")
    if source == "exam" and exam_id is None:
        raise BadRequest("exam_id is required for an exam's result")
    if sides not in (1, 2):
        raise BadRequest("sides must be 1 or 2")
    title = (title or "").strip()[:160]
    if not title:
        raise BadRequest("Give the release a title, e.g. \"Half-yearly exam 2026\"")
    student_ids = _parse_students(students_raw)

    path = _spool(file)
    try:
        try:
            src = pikepdf.open(path)
        except Exception:  # noqa: BLE001 - broken / encrypted file
            raise UnsupportedMediaType("This PDF could not be read. Save it again from the print window.") from None
        with src:
            if src.is_encrypted:
                raise UnsupportedMediaType("This PDF is password-protected. Save it again without a password.")
            _check_pages(src, len(student_ids) * sides, len(student_ids), sides, page_w_mm, page_h_mm)
            return _store_all(db, actor, src, source=source, exam_id=exam_id, title=title,
                              student_ids=student_ids, sides=sides, front_template_id=front_template_id,
                              back_template_id=back_template_id, send_notice=send_notice)
    finally:
        os.unlink(path)


def _store_all(db: Db, actor: CurrentUser, src, *, source, exam_id, title, student_ids, sides,
               front_template_id, back_template_id, send_notice) -> dict:
    with db.transaction():
        # the school's row is the lock: two releases at once can't race on the same students
        db.one("SELECT id FROM organisations WHERE id = %s FOR UPDATE", (db.org,))
        session = None
        if source == "exam":
            exam = db.one("SELECT id, name FROM exams WHERE id = %s AND org_id = %s", (exam_id, db.org))
            if not exam:
                raise NotFound("Exam not found")
            result_key = f"exam:{exam_id}"
        else:
            exam_id = None
            session = school_profile.current_session(db)["label"]
            result_key = f"final:{session}"
        for template_id in (front_template_id, back_template_id):
            if template_id is not None and not db.one(
                    "SELECT id FROM print_templates WHERE id = %s AND org_id = %s", (template_id, db.org)):
                raise NotFound("Design not found")

        marks = ", ".join(["%s"] * len(student_ids))
        people = {r["id"]: r for r in db.all(
            f"SELECT u.id, u.full_name, sp.class_id, sp.section_id, sp.last_class_id, sp.last_section_id "
            f"FROM users u LEFT JOIN student_profiles sp ON sp.user_id = u.id "
            f"WHERE u.org_id = %s AND u.user_type = 'student' AND u.id IN ({marks})", (db.org, *student_ids))}
        if len(people) != len(student_ids):
            raise NotFound("Student not found")
        existing = {r["student_id"]: r for r in db.all(
            f"SELECT id, student_id, file_path FROM report_card_files WHERE org_id = %s AND result_key = %s "
            f"AND student_id IN ({marks}) FOR UPDATE", (db.org, result_key, *student_ids))}

        store = storage.get_store()
        new_items, updated_items, notices, total_bytes = [], [], [], 0
        for index, sid in enumerate(student_ids):
            person = people[sid]
            data = compress.shrink_pdf(
                _one_student_pdf(src, index * sides, sides, f"Report card - {person['full_name']} - {title}"))
            name = f"{secrets.token_hex(16)}.pdf"
            key = f"{SUBDIR}/{name}"
            store.put(key, data, "application/pdf")
            db.on_rollback(lambda key=key: store.delete(key))
            stored_path = os.path.join(settings.upload_dir, SUBDIR, name)
            size = len(data)
            total_bytes += size
            class_id = person["class_id"] or person["last_class_id"]
            section_id = person["section_id"] or person["last_section_id"]
            old = existing.get(sid)
            if old:
                db.execute(
                    "UPDATE report_card_files SET source = %s, exam_id = %s, session = %s, class_id = %s, "
                    "section_id = %s, title = %s, file_path = %s, file_size = %s, pages = %s, "
                    "front_template_id = %s, back_template_id = %s, released_by = %s, "
                    "released_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (source, exam_id, session, class_id, section_id, title, stored_path, size, sides,
                     front_template_id, back_template_id, actor.id, old["id"]))
                storage.delete_after_commit(db, old["file_path"])
                file_id = old["id"]
                updated_items.append(sid)
            else:
                file_id = db.execute(
                    "INSERT INTO report_card_files (org_id, student_id, result_key, source, exam_id, session, "
                    "class_id, section_id, title, file_path, file_size, pages, front_template_id, "
                    "back_template_id, released_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (db.org, sid, result_key, source, exam_id, session, class_id, section_id, title, stored_path,
                     size, sides, front_template_id, back_template_id, actor.id)).lastrowid
                new_items.append(sid)
            notices.append(notify.Item(sid, f"📄 Report card {'updated' if old else 'ready'}",
                                       f"{title} · tap to download",
                                       {"route": "ReportCards", "params": {"report_card_id": file_id}}))
        if send_notice:
            notify.notify_each(db, notices, "report_card")
        audit.record(db, actor, "report_card.release", "report_card_files", None,
                     {"result": result_key, "title": title, "students": len(student_ids),
                      "new": len(new_items), "replaced": len(updated_items), "sides": sides,
                      "bytes": total_bytes})
    return {"released": len(student_ids), "new": len(new_items), "replaced": len(updated_items),
            "result_key": result_key, "session": session, "bytes": total_bytes,
            "files": list_files(db, result_key=result_key, student_ids=student_ids)}


# ------------------------------------------------------------------- lists

def list_files(db: Db, *, result_key: str | None = None, exam_id: int | None = None, session: str | None = None,
               class_id: int | None = None, student_ids: list[int] | None = None) -> list[dict]:
    sql, params = _SELECT, [db.org]
    if result_key:
        sql += " AND f.result_key = %s"
        params.append(result_key)
    if exam_id is not None:
        sql += " AND f.result_key = %s"
        params.append(f"exam:{exam_id}")
    if session:
        sql += " AND f.result_key = %s"
        params.append(f"final:{session}")
    if class_id is not None:
        sql += " AND f.class_id = %s"
        params.append(class_id)
    if student_ids:
        sql += f" AND f.student_id IN ({', '.join(['%s'] * len(student_ids))})"
        params.extend(student_ids)
    rows = db.all(sql + " ORDER BY c.name, s.name, CAST(sp.roll_no AS UNSIGNED), u.full_name LIMIT 2000", params)
    return [_out(r) for r in rows]


def summary(db: Db) -> list[dict]:
    """Every release of the school, newest first: one line per result."""
    rows = db.all(
        "SELECT f.result_key, f.source, f.exam_id, e.name AS exam_name, f.session, MAX(f.title) AS title, "
        "COUNT(*) AS students, SUM(f.file_size) AS bytes, MAX(f.released_at) AS released_at "
        "FROM report_card_files f LEFT JOIN exams e ON e.id = f.exam_id WHERE f.org_id = %s "
        "GROUP BY f.result_key, f.source, f.exam_id, e.name, f.session ORDER BY released_at DESC LIMIT 200",
        (db.org,))
    for r in rows:
        r["students"] = int(r["students"] or 0)
        r["bytes"] = int(r["bytes"] or 0)
    return rows


def withdraw(db: Db, actor: CurrentUser, file_id: int) -> None:
    with db.transaction():
        row = db.one("SELECT id, student_id, result_key, file_path FROM report_card_files "
                     "WHERE id = %s AND org_id = %s FOR UPDATE", (file_id, db.org))
        if not row:
            raise NotFound("Report card not found")
        db.execute("DELETE FROM report_card_files WHERE id = %s", (file_id,))
        storage.delete_after_commit(db, row["file_path"])
        audit.record(db, actor, "report_card.withdraw", "report_card_files", file_id,
                     {"student_id": row["student_id"], "result": row["result_key"]})


def withdraw_result(db: Db, actor: CurrentUser, result_key: str) -> int:
    with db.transaction():
        rows = db.all("SELECT id, file_path FROM report_card_files WHERE org_id = %s AND result_key = %s FOR UPDATE",
                      (db.org, result_key))
        if not rows:
            raise NotFound("Nothing released for this result")
        db.execute("DELETE FROM report_card_files WHERE org_id = %s AND result_key = %s", (db.org, result_key))
        for r in rows:
            storage.delete_after_commit(db, r["file_path"])
        audit.record(db, actor, "report_card.withdraw", "report_card_files", None,
                     {"result": result_key, "students": len(rows)})
    return len(rows)


def for_student(db: Db, uid: int) -> list[dict]:
    """The parent's report cards, newest first."""
    rows = db.all(_SELECT + " AND f.student_id = %s ORDER BY f.released_at DESC, f.id DESC LIMIT 50", (db.org, uid))
    return [_out(r) for r in rows]


def student_files(db: Db, user: CurrentUser, student_id: int) -> list[dict]:
    """Staff view of one student's released report cards (same check as the student's other records)."""
    from app.services import students

    students._viewable_student(db, user, student_id)
    rows = db.all(_SELECT + " AND f.student_id = %s ORDER BY f.released_at DESC, f.id DESC LIMIT 50",
                  (db.org, student_id))
    return [_out(r) for r in rows]

