"""Exam results for a whole section at once: a marks sheet (students × subjects),
an Excel template to fill in, and the Excel upload (Nov 2026; CSV still read
for old clients).

Who:
* the sheet (read): managers + teachers assigned to the section (as GET /exams/{id}/results);
* template + upload: managers or the section's CLASS teacher.

Template (.xlsx, sheet "Marks" + a "How to fill" sheet):

    student_id | admission_no | roll_no | student_name | English | Mathematics | … | remarks

* one column per subject of the class (names as in the class's subject list,
  any letter case); the first four columns identify the student (grey, don't
  edit); `remarks` is optional (the remark for the whole exam);
* the MAXIMUM marks are NOT in the sheet: the teacher types them in the upload
  form (`max_marks`, required — "out of" for every subject of this upload);
* a student is found by `student_id`, else `admission_no`, else `roll_no` —
  only students of that section;
* a cell: a number from 0 to the maximum, `AB` = absent, empty = leave as it is.

The upload is all-or-nothing: any bad cell → 400 with `errors: [{row, column,
message}]` and nothing is saved. Marks land in exam_results exactly as if
typed in the web app per subject. A successful upload PUBLISHES the exam's
results (parents see them at once in Exams and the Performance Report) and
gives the exam a maximum if it had none.
"""
from __future__ import annotations

import base64
import re

from fastapi import UploadFile

from app.core.errors import BadRequest, Forbidden
from app.core.roles import MANAGERS
from app.db import Db
from app.models.auth import CurrentUser
from app.models.exams import MARKS_LIMIT
from app.services import access, audit, spreadsheet
from app.services import exams as exam_service

MAX_BYTES = 2 * 1024 * 1024
ABSENT = "AB"
_ABSENT_WORDS = {"ab", "abs", "absent"}
ID_COLUMNS = ("student_id", "admission_no", "roll_no", "student_name")
REMARKS = "remarks"


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _context(db: Db, user: CurrentUser, exam_id: int, section_id: int) -> tuple[dict, dict, list[str]]:
    exam = exam_service.get_exam(db, exam_id)
    section = exam_service._section_in_exam(db, exam, section_id)
    access.ensure_can_view_section(db, user, section["class_id"], section_id)
    subjects = exam_service._subjects(db, exam_id, section["class_id"])
    return exam, section, subjects


def can_import(db: Db, user: CurrentUser, class_id: int, section_id: int) -> bool:
    return user.user_type in MANAGERS or (
        user.user_type == "teacher" and access.is_class_teacher(db, user, class_id, section_id))


def _ensure_can_import(db: Db, user: CurrentUser, section: dict) -> None:
    if not can_import(db, user, section["class_id"], section["id"]):
        raise Forbidden("Only the class teacher of this section (or admins) can upload results")


def _students(db: Db, section_id: int) -> list[dict]:
    return db.all(
        "SELECT u.id AS student_id, u.full_name, u.photo_path, sp.roll_no, sp.admission_no "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        "WHERE sp.org_id = %s AND sp.section_id = %s AND u.is_active = 1 "
        "ORDER BY CAST(sp.roll_no AS UNSIGNED), sp.roll_no, u.full_name",
        (db.org, section_id),
    )


def _saved(db: Db, exam_id: int, student_ids: list[int]) -> list[dict]:
    if not student_ids:
        return []
    return db.all(
        "SELECT student_id, subject, marks, max_marks, grade, remarks FROM exam_results "
        f"WHERE exam_id = %s AND student_id IN ({','.join(['%s'] * len(student_ids))})",
        (exam_id, *student_ids),
    )


def _subject_max(saved: list[dict], subjects: list[str], exam: dict) -> dict[str, float | None]:
    out: dict[str, float | None] = {s: None for s in subjects}
    for r in saved:
        if r["subject"] in out and out[r["subject"]] is None and r["max_marks"] is not None:
            out[r["subject"]] = float(r["max_marks"])
    default = _num(exam.get("max_marks"))
    return {s: (m if m is not None else default) for s, m in out.items()}


def sheet(db: Db, user: CurrentUser, exam_id: int, section_id: int) -> dict:
    """The section's marks for every subject in one table (+ totals), and
    whether the caller may upload a CSV."""
    exam, section, subjects = _context(db, user, exam_id, section_id)
    students = _students(db, section_id)
    saved = _saved(db, exam_id, [s["student_id"] for s in students])
    maxima = _subject_max(saved, subjects, exam)
    by_student: dict[int, dict[str, dict]] = {}
    for r in saved:
        by_student.setdefault(r["student_id"], {})[r["subject"]] = r
    rows = []
    for st in students:
        mine = by_student.get(st["student_id"], {})
        marks: dict[str, float | str | None] = {}
        total = max_total = 0.0
        counted = 0
        for sub in subjects:
            r = mine.get(sub)
            if r is None or (r["marks"] is None and not r["grade"]):
                marks[sub] = None
                continue
            absent = r["marks"] is None and (r["grade"] or "").upper() == ABSENT
            marks[sub] = ABSENT if absent else (_num(r["marks"]) if r["marks"] is not None else None)
            top = _num(r["max_marks"]) if r["max_marks"] is not None else maxima.get(sub)
            if (absent or r["marks"] is not None) and top:
                total += 0.0 if absent else float(r["marks"])
                max_total += top
                counted += 1
        whole = mine.get("")
        rows.append({
            **st,
            "marks": marks,
            "remarks": whole["remarks"] if whole else None,
            "total": total if counted else None,
            "max_total": max_total if counted else None,
            "percentage": round(total * 100 / max_total, 1) if counted and max_total else None,
        })
    return {
        "exam": {k: exam[k] for k in ("id", "name", "start_date", "end_date", "status", "results_published")}
        | {"max_marks": _num(exam.get("max_marks"))},
        "section": {"id": section_id, "class_id": section["class_id"],
                    **(db.one("SELECT s.name AS section_name, c.name AS class_name FROM sections s "
                              "JOIN classes c ON c.id = s.class_id WHERE s.id = %s", (section_id,)) or {})},
        "subjects": [{"name": s, "max_marks": maxima.get(s)} for s in subjects],
        "students": rows,
        "can_import": can_import(db, user, section["class_id"], section_id),
    }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower() or "results"


def template(db: Db, user: CurrentUser, exam_id: int, section_id: int) -> tuple[str, bytes]:
    """(file name, .xlsx bytes) — the section's students, the subjects as
    columns and any marks already saved, so a re-upload changes nothing."""
    data = sheet(db, user, exam_id, section_id)
    if not data["can_import"]:
        raise Forbidden("Only the class teacher of this section (or admins) can upload results")
    subjects = [s["name"] for s in data["subjects"]]
    if not subjects:
        raise BadRequest("This class has no subjects yet — add them (Classes → Subjects) before uploading marks")
    marks_rule = 'OR(AND(ISNUMBER({cell}),{cell}>=0),UPPER(TRIM({cell}))="AB")'
    columns = [
        spreadsheet.Column("student_id", 11, locked_look=True, note="Don't change — identifies the student"),
        spreadsheet.Column("admission_no", 14, text=True, locked_look=True),
        spreadsheet.Column("roll_no", 9, text=True, locked_look=True),
        spreadsheet.Column("student_name", 26, locked_look=True),
        *(spreadsheet.Column(s, max(12, min(24, len(s) + 4)), validation=marks_rule,
                             validation_error="Type the marks (a number) or AB for absent.") for s in subjects),
        spreadsheet.Column(REMARKS, 30),
    ]
    rows = []
    for st in data["students"]:
        cells = [st["marks"].get(s) for s in subjects]
        rows.append([st["student_id"], st["admission_no"] or "", st["roll_no"] or "", st["full_name"],
                     *cells, st["remarks"] or ""])
    sec = data["section"]
    title = f"{data['exam']['name']} · {sec.get('class_name', '')} {sec.get('section_name', '')}".strip()
    content = spreadsheet.workbook(spreadsheet.Sheet(
        title="Marks", columns=columns, rows=rows,
        help=[
            f"How to fill the marks sheet — {title}",
            "1. Fill in the marks of every student under each subject column on the 'Marks' sheet.",
            "2. Type AB for a student who was absent. Leave a cell empty to keep the mark already saved.",
            "3. Do not change the grey columns (student_id, admission_no, roll_no, student_name) "
            "or the subject names in the first row.",
            "4. The remarks column is optional (one remark per student for the whole exam).",
            "5. The maximum marks are NOT typed here: you enter them (e.g. 50 or 100) when you upload the file.",
            "6. Save the file as Excel (.xlsx) and upload it on the exam's Results page. "
            "If any cell is wrong, nothing is saved and the rows to fix are listed.",
        ],
    ))
    parts = (data["exam"]["name"], sec.get("class_name", ""), sec.get("section_name", ""))
    return "results-" + "-".join(_slug(p) for p in parts) + ".xlsx", content


def template_base64(db: Db, user: CurrentUser, exam_id: int, section_id: int) -> dict:
    """For the mobile app (it saves the file itself): {filename, mime, content_base64}."""
    name, content = template(db, user, exam_id, section_id)
    return {"filename": name, "mime": spreadsheet.XLSX_MIME, "content_base64": base64.b64encode(content).decode()}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def import_results(db: Db, user: CurrentUser, exam_id: int, section_id: int, file: UploadFile,
                   max_marks: float) -> dict:
    """Save the marks in the uploaded Excel sheet; `max_marks` = what every
    subject of this upload is out of (asked in the upload form, required)."""
    if max_marks is None or not 0 < max_marks < MARKS_LIMIT:
        raise BadRequest("Enter the maximum marks (out of) for this exam, e.g. 50 or 100")
    raw = file.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise BadRequest("The file is larger than 2 MB")
    rows = spreadsheet.read_rows(raw, what="marks file")

    with db.transaction():
        exam, section, subjects = _context(db, user, exam_id, section_id)
        _ensure_can_import(db, user, section)
        if not subjects:
            raise BadRequest("This class has no subjects yet — add them (Classes → Subjects) before uploading marks")

        header = [_norm(h) for h in rows[0]]
        known = {_norm(s): s for s in subjects}
        col_subject: dict[int, str] = {}
        col_id: dict[str, int] = {}
        errors: list[dict] = []
        for i, h in enumerate(header):
            if not h:
                continue
            key = h.replace(" ", "_")
            if key in ID_COLUMNS or key in ("name", "admission_number", "roll", "roll_number"):
                col_id[{"name": "student_name", "admission_number": "admission_no", "roll": "roll_no",
                        "roll_number": "roll_no"}.get(key, key)] = i
            elif key == REMARKS or key == "remark":
                col_id[REMARKS] = i
            elif h in known:
                if known[h] in col_subject.values():
                    errors.append({"row": 1, "column": rows[0][i], "message": "Subject column appears twice"})
                col_subject[i] = known[h]
            else:
                errors.append({"row": 1, "column": rows[0][i],
                               "message": f"Not a subject of this class (subjects: {', '.join(subjects)})"})
        if not any(k in col_id for k in ("student_id", "admission_no", "roll_no")):
            errors.append({"row": 1, "column": "", "message": "Add a student_id, admission_no or roll_no column"})
        if not col_subject and REMARKS not in col_id:
            errors.append({"row": 1, "column": "", "message": "No subject columns found"})
        if errors:
            _fail(errors)

        students = _students(db, section_id)
        by_id = {str(s["student_id"]): s for s in students}
        by_adm = {_norm(s["admission_no"]): s for s in students if s["admission_no"]}
        by_roll = {_norm(str(s["roll_no"])): s for s in students if s["roll_no"] not in (None, "")}
        maxima = {s: float(max_marks) for s in subjects}

        def cell(row: list[str], i: int | None) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        first_col = min(col_id.get(k, 10**6) for k in ("student_id", "admission_no", "roll_no"))
        data_rows: list[tuple[int, list[str]]] = []
        for n, row in enumerate(rows[1:], start=2):
            if not any(c.strip() for c in row):
                continue
            if any(cell(row, col_id.get(k)).upper() == "MAX" for k in ("student_id", "admission_no", "roll_no")) \
                    or cell(row, first_col).upper() == "MAX":
                continue  # the MAX row of the old CSV template: the maximum now comes from the form
            data_rows.append((n, row))

        entries: list[tuple] = []
        remarks: list[tuple] = []
        seen: dict[int, int] = {}
        absent_n = 0
        for n, row in data_rows:
            sid, adm, roll = cell(row, col_id.get("student_id")), cell(row, col_id.get("admission_no")), \
                cell(row, col_id.get("roll_no"))
            st = by_id.get(sid) if sid else None
            if st is None and adm:
                st = by_adm.get(_norm(adm))
            if st is None and roll:
                st = by_roll.get(_norm(roll))
            if st is None:
                who = sid or adm or roll or cell(row, col_id.get("student_name")) or "?"
                errors.append({"row": n, "column": "", "message": f"Student '{who}' is not in this section"})
                continue
            if st["student_id"] in seen:
                errors.append({"row": n, "column": "",
                               "message": f"{st['full_name']} is already on row {seen[st['student_id']]}"})
                continue
            seen[st["student_id"]] = n
            for i, sub in col_subject.items():
                v = cell(row, i)
                if not v:
                    continue
                if v.lower() in _ABSENT_WORDS:
                    entries.append((exam_id, st["student_id"], sub, None, maxima.get(sub), ABSENT))
                    absent_n += 1
                    continue
                try:
                    marks = float(v)
                except ValueError:
                    errors.append({"row": n, "column": sub, "message": f"'{v}' is not a number (use AB for absent)"})
                    continue
                top = maxima.get(sub)
                if marks < 0 or marks >= MARKS_LIMIT or (top is not None and marks > top):
                    limit = f"0 and {top:g}" if top is not None else f"0 and {MARKS_LIMIT - 1}"
                    errors.append({"row": n, "column": sub, "message": f"{v} — marks must be between {limit}"})
                    continue
                entries.append((exam_id, st["student_id"], sub, marks, top, None))
            if REMARKS in col_id:
                text_ = cell(row, col_id[REMARKS])[:255]
                if text_:
                    remarks.append((exam_id, st["student_id"], text_))
        if errors:
            _fail(errors)
        if not entries and not remarks:
            raise BadRequest("The file has no marks to save")

        if entries:
            db.execute_many(
                "INSERT INTO exam_results (exam_id, student_id, subject, marks, max_marks, grade) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE marks = VALUES(marks), max_marks = VALUES(max_marks), "
                # AB sets the grade; a number keeps a grade typed earlier (but clears an old AB)
                "grade = IF(VALUES(marks) IS NULL, VALUES(grade), IF(grade = 'AB', NULL, grade))",
                entries,
            )
        if remarks:
            db.execute_many(
                "INSERT INTO exam_results (exam_id, student_id, subject, remarks) VALUES (%s, %s, '', %s) "
                "ON DUPLICATE KEY UPDATE remarks = VALUES(remarks)",
                remarks,
            )
        # uploaded results are for parents to see: publish them (managers can still unpublish),
        # and remember the maximum on the exam when it had none (the default for typed marks)
        published_now = not exam["results_published"]
        db.execute("UPDATE exams SET results_published = 1, max_marks = COALESCE(max_marks, %s) WHERE id = %s",
                   (max_marks, exam_id))
        audit.record(db, user, "exam_results.import", "exam", exam_id,
                     {"section_id": section_id, "students": len(seen), "marks": len(entries),
                      "absent": absent_n, "max_marks": max_marks, "published": published_now,
                      "subjects": sorted(set(col_subject.values()))})
    return {"saved": True, "students": len(seen), "marks": len(entries), "absent": absent_n,
            "remarks": len(remarks), "max_marks": float(max_marks), "published": True,
            "subjects": [s for s in subjects if s in col_subject.values()]}


def _fail(errors: list[dict]) -> None:
    first = errors[0]
    where = f"row {first['row']}" + (f", {first['column']}" if first.get("column") else "")
    more = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
    raise BadRequest(f"Nothing was saved — fix the file: {where}: {first['message']}{more}",
                     extra={"code": "file_invalid", "errors": errors[:200]})
