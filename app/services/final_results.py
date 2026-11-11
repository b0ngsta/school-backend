"""Final result (migration 0021): a class's exam format (services/exam_format.py)
lists the exams of the session with the marks each carries; the final result
of a subject is the sum of the student's marks in those exams —

    Maths: Notebook 4/5 + Handwriting 5/5 + Term 1 16/20 + Final exam 60/70 = 85/100

— graded with the school's grading system (services/grading.py).

    GET  /final-results?section_id=             sheet (staff who can view the section)
    GET  /final-results/template?section_id=    Excel, marks already saved are prefilled
    POST /final-results/import                  the filled Excel (class teacher / managers)
    PUT  /final-results/publish                 {section_id, published}: show it to parents
    GET  /student/final-result                  the parent's view (only once published)
    GET  /students/{id}/exam-results            a student's exams + final result (staff)

Marks live in exam_results, exactly where each exam's own results live: the
Excel of the final result is one more way to fill them in, for every part at
once. A mark entered against another maximum than the part carries (Term 1
marked out of 50 but worth 20) is scaled in the final result; the Excel shows
the scaled value and only writes the cells that were changed.
"""
from __future__ import annotations

import base64
import re

from fastapi import UploadFile

from app.core.errors import BadRequest, Forbidden, NotFound
from app.db import Db
from app.models.auth import CurrentUser
from app.models.exams import FinalPublish
from app.services import access, audit, exam_format, grading, labels, notify, school_profile, spreadsheet
from app.services import students as student_service
from app.services.exam_results_import import (
    _ABSENT_WORDS,
    ABSENT,
    ID_COLUMNS,
    MAX_BYTES,
    _fail,
    _norm,
    _slug,
    _students,
    can_import,
)
from app.services.exams import EXAM_STATUS

Value = float | str | None  # marks in the part's scale · "AB" · nothing yet


def _section(db: Db, section_id: int) -> dict:
    section = db.one(
        "SELECT s.id, s.class_id, s.name AS section_name, c.name AS class_name, c.has_sections "
        "FROM sections s JOIN classes c ON c.id = s.class_id WHERE s.id = %s AND s.org_id = %s",
        (section_id, db.org))
    if not section:
        raise NotFound("Section not found")
    section["label"] = labels.class_section(section["class_name"], section["section_name"])
    return section


def _subjects(db: Db, class_id: int, exam_ids: list[int]) -> list[str]:
    names = [r["name"] for r in db.all("SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id", (class_id,))]
    if exam_ids:
        used = db.all(
            "SELECT DISTINCT r.subject FROM exam_results r JOIN student_profiles sp ON sp.user_id = r.student_id "
            f"WHERE r.exam_id IN ({','.join(['%s'] * len(exam_ids))}) AND r.subject <> '' AND sp.class_id = %s",
            (*exam_ids, class_id))
        names += [r["subject"] for r in used if r["subject"] not in names]
    return names


def _cells(db: Db, parts: list[dict], student_ids: list[int]) -> dict[tuple[int, int, str], Value]:
    """(student, exam, subject) → the mark in the part's scale, or "AB"."""
    if not parts or not student_ids:
        return {}
    by_exam = {p["exam_id"]: p for p in parts}
    rows = db.all(
        "SELECT exam_id, student_id, subject, marks, max_marks, grade FROM exam_results "
        f"WHERE exam_id IN ({','.join(['%s'] * len(by_exam))}) "
        f"AND student_id IN ({','.join(['%s'] * len(student_ids))}) AND subject <> ''",
        (*by_exam, *student_ids))
    out: dict[tuple[int, int, str], Value] = {}
    for r in rows:
        part = by_exam[r["exam_id"]]
        if r["marks"] is None:
            if (r["grade"] or "").upper() == ABSENT:
                out[(r["student_id"], r["exam_id"], r["subject"])] = ABSENT
            continue
        own = float(r["max_marks"]) if r["max_marks"] is not None else (part["exam_max_marks"] or part["max_marks"])
        marks = float(r["marks"])
        if own and abs(own - part["max_marks"]) > 0.0001:
            marks = marks * part["max_marks"] / own
        out[(r["student_id"], r["exam_id"], r["subject"])] = round(min(marks, part["max_marks"]), 2)
    return out


def _pct(got: float, top: float) -> float | None:
    return round(got * 100 / top, 1) if top else None


def _rows(parts: list[dict], subjects: list[str], students: list[dict], cells: dict, grade) -> list[dict]:
    rows = []
    for st in students:
        sid = st["student_id"]
        per_subject: dict[str, dict] = {}
        total = max_total = 0.0
        any_mark, complete = False, bool(subjects) and bool(parts)
        for sub in subjects:
            values = {str(p["exam_id"]): cells.get((sid, p["exam_id"], sub)) for p in parts}
            got = sum(v for v in values.values() if isinstance(v, int | float))
            top = sum(p["max_marks"] for p in parts if values[str(p["exam_id"])] is not None)
            full = all(v is not None for v in values.values())
            pct = _pct(got, top)
            per_subject[sub] = {"parts": values, "total": round(got, 2) if top else None,
                                "max_total": round(top, 2) if top else None, "percentage": pct,
                                "grade": grade(pct), "complete": full}
            if top:
                any_mark = True
                total += got
                max_total += top
            complete = complete and full
        pct = _pct(total, max_total)
        rows.append({**st, "subjects": per_subject,
                     "total": round(total, 2) if any_mark else None,
                     "max_total": round(max_total, 2) if any_mark else None,
                     "percentage": pct, "grade": grade(pct), "complete": complete, "rank": None})
    scores = [r["percentage"] for r in rows if r["percentage"] is not None]
    for r in rows:
        if r["percentage"] is not None:
            r["rank"] = 1 + sum(1 for s in scores if s > r["percentage"] + 1e-9)
    return rows


def _published(db: Db, section_id: int, session: str) -> dict | None:
    return db.one(
        "SELECT fr.published_at, u.full_name AS published_by_name FROM final_results fr "
        "LEFT JOIN users u ON u.id = fr.published_by WHERE fr.org_id = %s AND fr.section_id = %s AND fr.session = %s",
        (db.org, section_id, session))


def _part_view(p: dict) -> dict:
    return {"exam_id": p["exam_id"], "name": p["name"], "max_marks": p["max_marks"], "status": p["status"],
            "results_published": p["results_published"], "all_classes": p["all_classes"]}


def _build(db: Db, section: dict) -> dict:
    sess = school_profile.current_session(db)
    parts = exam_format.parts(db, section["class_id"], sess["label"])
    subjects = _subjects(db, section["class_id"], [p["exam_id"] for p in parts])
    students = _students(db, section["id"])
    cells = _cells(db, parts, [s["student_id"] for s in students])
    rows = _rows(parts, subjects, students, cells, grading.grader(db))
    published = _published(db, section["id"], sess["label"])
    return {
        "session": sess["label"],
        "class": {"id": section["class_id"], "name": section["class_name"],
                  "has_sections": section["has_sections"]},
        "section": {"id": section["id"], "name": section["section_name"], "label": section["label"]},
        "parts": [_part_view(p) for p in parts],
        "format_total": round(sum(p["max_marks"] for p in parts), 2),
        "subjects": subjects,
        "students": rows,
        "incomplete": sum(1 for r in rows if not r["complete"]),
        "published": published is not None,
        "published_at": published["published_at"] if published else None,
        "published_by_name": published["published_by_name"] if published else None,
        "grading": grading.get(db)["bands"],
        "_parts": parts, "_cells": cells,
    }


def _public(data: dict) -> dict:
    return {k: v for k, v in data.items() if not k.startswith("_")}


def sheet(db: Db, user: CurrentUser, section_id: int) -> dict:
    section = _section(db, section_id)
    access.ensure_can_view_section(db, user, section["class_id"], section_id)
    data = _public(_build(db, section))
    data["can_edit"] = can_import(db, user, section["class_id"], section_id)
    return data


def _editable(db: Db, user: CurrentUser, section_id: int) -> tuple[dict, dict]:
    section = _section(db, section_id)
    access.ensure_can_view_section(db, user, section["class_id"], section_id)
    if not can_import(db, user, section["class_id"], section_id):
        raise Forbidden("Only the class teacher of this class (or the office) can fill in the final result")
    data = _build(db, section)
    if not data["parts"]:
        raise BadRequest(f"{section['class_name']} has no exam format yet — the office sets it under "
                         "Exams → Exam format")
    if not data["subjects"]:
        raise BadRequest("This class has no subjects yet — add them (Classes → Subjects) first")
    return section, data


def _header(subject: str, part: dict) -> str:
    return f"{subject} | {part['name']} ({part['max_marks']:g})"


def template(db: Db, user: CurrentUser, section_id: int) -> tuple[str, bytes]:
    """(file name, .xlsx bytes): one column per subject and exam, with every
    mark already saved filled in (published results included)."""
    section, data = _editable(db, user, section_id)
    parts, subjects = data["_parts"], data["subjects"]
    rule = 'OR(AND(ISNUMBER({cell}),{cell}>=0),UPPER(TRIM({cell}))="AB")'
    columns = [
        spreadsheet.Column("student_id", 11, locked_look=True, note="Don't change — identifies the student"),
        spreadsheet.Column("admission_no", 14, text=True, locked_look=True),
        spreadsheet.Column("roll_no", 9, text=True, locked_look=True),
        spreadsheet.Column("student_name", 26, locked_look=True),
    ]
    for sub in subjects:
        for p in parts:
            head = _header(sub, p)
            columns.append(spreadsheet.Column(
                head, max(14, min(30, len(head) + 2)), validation=rule,
                validation_error=f"Type the marks (0 to {p['max_marks']:g}) or AB for absent.",
                note=("Published already — filled in for you. Change it only to correct a mistake."
                      if p["results_published"] else f"Out of {p['max_marks']:g}")))
    rows = []
    for st in data["students"]:
        cells = [st["subjects"][sub]["parts"][str(p["exam_id"])] for sub in subjects for p in parts]
        rows.append([st["student_id"], st["admission_no"] or "", st["roll_no"] or "", st["full_name"], *cells])
    done = [p["name"] for p in parts if p["results_published"]]
    content = spreadsheet.workbook(spreadsheet.Sheet(
        title="Final result", columns=columns, rows=rows,
        help=[
            f"How to fill the final result — {section['label']} · {data['session']}",
            "1. Each subject has one column per exam of the class's exam format: "
            + ", ".join(f"{p['name']} (out of {p['max_marks']:g})" for p in parts) + ".",
            ("2. Marks already saved are filled in for you" + (f" — published: {', '.join(done)}" if done else "")
             + ". Fill in the empty cells."),
            "3. Type AB for a student who was absent. A cell you leave empty (or don't change) stays as it is.",
            "4. Do not change the grey columns or the names in the first row.",
            "5. Save the file as Excel (.xlsx) and upload it on the Final result page. "
            "If any cell is wrong, nothing is saved and the rows to fix are listed.",
            "6. After uploading, check the table and press Publish to show the final result to parents.",
        ],
    ))
    name = "final-result-" + "-".join(_slug(x) for x in (section["class_name"], section["section_name"]) if x)
    return f"{name}.xlsx", content


def template_base64(db: Db, user: CurrentUser, section_id: int) -> dict:
    name, content = template(db, user, section_id)
    return {"filename": name, "mime": spreadsheet.XLSX_MIME, "content_base64": base64.b64encode(content).decode()}


_HEAD = re.compile(r"^(?P<subject>.+?)\s*\|\s*(?P<part>.+?)\s*(?:\(\s*[\d.]+\s*\))?$")


def import_results(db: Db, user: CurrentUser, section_id: int, file: UploadFile) -> dict:
    """Save the changed cells of the uploaded final-result sheet (all or nothing)."""
    raw = file.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise BadRequest("The file is larger than 2 MB")
    rows = spreadsheet.read_rows(raw, what="final result file")
    with db.transaction():
        section, data = _editable(db, user, section_id)
        parts, cells = data["_parts"], data["_cells"]
        known_subject = {_norm(s): s for s in data["subjects"]}
        known_part = {_norm(p["name"]): p for p in parts}
        col_mark: dict[int, tuple[str, dict]] = {}
        col_id: dict[str, int] = {}
        errors: list[dict] = []
        for i, title in enumerate(rows[0]):
            h = _norm(title)
            if not h:
                continue
            key = h.replace(" ", "_")
            if key in ID_COLUMNS or key in ("name", "admission_number", "roll", "roll_number"):
                col_id[{"name": "student_name", "admission_number": "admission_no", "roll": "roll_no",
                        "roll_number": "roll_no"}.get(key, key)] = i
                continue
            m = _HEAD.match(title.strip())
            sub = known_subject.get(_norm(m["subject"])) if m else None
            part = known_part.get(_norm(m["part"])) if m else None
            if not m or sub is None or part is None:
                errors.append({"row": 1, "column": title,
                               "message": "Column not recognised — download a fresh sheet and fill that one"})
            elif (sub, part["exam_id"]) in {(s, p["exam_id"]) for s, p in col_mark.values()}:
                errors.append({"row": 1, "column": title, "message": "This column appears twice"})
            else:
                col_mark[i] = (sub, part)
        if not any(k in col_id for k in ("student_id", "admission_no", "roll_no")):
            errors.append({"row": 1, "column": "", "message": "Add a student_id, admission_no or roll_no column"})
        if not col_mark:
            errors.append({"row": 1, "column": "", "message": "No marks columns found"})
        if errors:
            _fail(errors)

        students = data["students"]
        by_id = {str(s["student_id"]): s for s in students}
        by_adm = {_norm(s["admission_no"]): s for s in students if s["admission_no"]}
        by_roll = {_norm(str(s["roll_no"])): s for s in students if s["roll_no"] not in (None, "")}

        def cell(row: list[str], i: int | None) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        entries: list[tuple] = []
        seen: dict[int, int] = {}
        unchanged = 0
        for n, row in enumerate(rows[1:], start=2):
            if not any(c.strip() for c in row):
                continue
            sid, adm, roll = (cell(row, col_id.get(k)) for k in ("student_id", "admission_no", "roll_no"))
            st = by_id.get(sid) if sid else None
            if st is None and adm:
                st = by_adm.get(_norm(adm))
            if st is None and roll:
                st = by_roll.get(_norm(roll))
            if st is None:
                who = sid or adm or roll or cell(row, col_id.get("student_name")) or "?"
                errors.append({"row": n, "column": "", "message": f"Student '{who}' is not in this class"})
                continue
            if st["student_id"] in seen:
                errors.append({"row": n, "column": "",
                               "message": f"{st['full_name']} is already on row {seen[st['student_id']]}"})
                continue
            seen[st["student_id"]] = n
            for i, (sub, part) in col_mark.items():
                v = cell(row, i)
                if not v:
                    continue
                now = cells.get((st["student_id"], part["exam_id"], sub))
                if v.lower() in _ABSENT_WORDS:
                    if now == ABSENT:
                        unchanged += 1
                    else:
                        entries.append((part["exam_id"], st["student_id"], sub, None, part["max_marks"], ABSENT))
                    continue
                try:
                    marks = float(v)
                except ValueError:
                    errors.append({"row": n, "column": rows[0][i],
                                   "message": f"'{v}' is not a number (use AB for absent)"})
                    continue
                if marks < 0 or marks > part["max_marks"] + 0.0001:
                    errors.append({"row": n, "column": rows[0][i],
                                   "message": f"{v} — marks must be between 0 and {part['max_marks']:g}"})
                    continue
                if isinstance(now, int | float) and abs(now - marks) < 0.005:
                    unchanged += 1
                    continue
                entries.append((part["exam_id"], st["student_id"], sub, marks, part["max_marks"], None))
        if errors:
            _fail(errors)
        if not entries and not unchanged:
            raise BadRequest("The file has no marks to save")
        if entries:
            db.execute_many(
                "INSERT INTO exam_results (exam_id, student_id, subject, marks, max_marks, grade) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE marks = VALUES(marks), max_marks = VALUES(max_marks), "
                "grade = IF(VALUES(marks) IS NULL, VALUES(grade), IF(grade = 'AB', NULL, grade))",
                entries)
        audit.record(db, user, "final_results.import", "section", section_id,
                     {"session": data["session"], "students": len(seen), "marks": len(entries),
                      "unchanged": unchanged, "exams": sorted({e[0] for e in entries})})
    out = sheet(db, user, section_id)
    out["saved"] = {"students": len(seen), "marks": len(entries), "unchanged": unchanged}
    return out


def set_published(db: Db, user: CurrentUser, body: FinalPublish) -> dict:
    """Show (or hide again) the section's final result to parents."""
    with db.transaction():
        section, data = _editable(db, user, body.section_id)
        if body.published:
            if not any(r["total"] is not None for r in data["students"]):
                raise BadRequest("There are no marks yet — fill in the final result before publishing it")
            first = not data["published"]
            db.execute(
                "INSERT INTO final_results (org_id, class_id, section_id, session, published_by) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE published_by = VALUES(published_by), published_at = NOW()",
                (db.org, section["class_id"], section["id"], data["session"], user.id))
            if first:
                notify.notify(db, [s["student_id"] for s in data["students"]], "exam_result",
                              "🎓 Final result published", f"{section['label']} · {data['session']}",
                              data={"route": "Exams"})
        else:
            db.execute("DELETE FROM final_results WHERE org_id = %s AND section_id = %s AND session = %s",
                       (db.org, section["id"], data["session"]))
        audit.record(db, user, "final_results.publish" if body.published else "final_results.unpublish",
                     "section", section["id"], {"session": data["session"], "class": section["label"]})
    return sheet(db, user, body.section_id)


def _student_view(data: dict, student_id: int) -> dict | None:
    """One student's final result out of a section's sheet."""
    row = next((r for r in data["students"] if r["student_id"] == student_id), None)
    if row is None:
        return None
    subjects = []
    for sub in data["subjects"]:
        s = row["subjects"][sub]
        subjects.append({"subject": sub, "total": s["total"], "max_total": s["max_total"],
                         "percentage": s["percentage"], "grade": s["grade"], "complete": s["complete"],
                         "parts": [{"exam_id": p["exam_id"], "name": p["name"], "max_marks": p["max_marks"],
                                    "marks": s["parts"][str(p["exam_id"])]} for p in data["parts"]]})
    return {
        "session": data["session"], "class_name": data["class"]["name"], "section_name": data["section"]["name"],
        "parts": data["parts"], "format_total": data["format_total"], "subjects": subjects,
        "total": row["total"], "max_total": row["max_total"], "percentage": row["percentage"],
        "grade": row["grade"], "rank": row["rank"], "complete": row["complete"],
        "class_size": sum(1 for r in data["students"] if r["percentage"] is not None),
        "published": data["published"], "published_at": data["published_at"], "grading": data["grading"],
    }


def for_student(db: Db, uid: int) -> dict:
    """The parent's view: `{published: false}` until the school publishes it."""
    me = db.one("SELECT section_id FROM student_profiles WHERE user_id = %s AND org_id = %s", (uid, db.org)) or {}
    session = school_profile.current_session(db)["label"]
    if not me.get("section_id") or not _published(db, me["section_id"], session):
        return {"published": False, "session": session}
    view = _student_view(_public(_build(db, _section(db, me["section_id"]))), uid)
    return view or {"published": False, "session": session}


def student_results(db: Db, user: CurrentUser, student_id: int) -> dict:
    """A student's results exam by exam (every exam they have marks in, newest
    first) + the final result of the session — for the office and the
    student's teachers (the "Exam result" section of the student's page)."""
    student = student_service.view_student(db, user, student_id)
    grade = grading.grader(db)
    exams = db.all(
        f"SELECT e.id, e.name, e.start_date, e.end_date, e.max_marks, e.results_published, {EXAM_STATUS} AS status "
        "FROM exams e WHERE e.org_id = %s AND e.id IN (SELECT r.exam_id FROM exam_results r WHERE r.student_id = %s) "
        "ORDER BY e.start_date DESC, e.id DESC", (db.org, student_id))
    marks = db.all("SELECT exam_id, subject, marks, max_marks, grade, remarks FROM exam_results "
                   "WHERE student_id = %s ORDER BY id", (student_id,)) if exams else []
    by_exam: dict[int, list[dict]] = {}
    for r in marks:
        by_exam.setdefault(r["exam_id"], []).append(r)
    out = []
    for e in exams:
        exam_max = float(e["max_marks"]) if e["max_marks"] is not None else None
        subjects, got, top, remarks = [], 0.0, 0.0, None
        whole = None
        for r in by_exam.get(e["id"], []):
            absent = r["marks"] is None and (r["grade"] or "").upper() == ABSENT
            own = float(r["max_marks"]) if r["max_marks"] is not None else exam_max
            value = float(r["marks"]) if r["marks"] is not None else None
            pct = _pct(0.0 if absent else value, own) if own and (absent or value is not None) else None
            entry = {"subject": r["subject"], "marks": value, "max_marks": own, "absent": absent,
                     "percentage": pct, "grade": ABSENT if absent else (r["grade"] or grade(pct))}
            if not r["subject"]:
                whole, remarks = entry, r["remarks"]
                continue
            if value is None and not absent:
                continue
            subjects.append(entry)
            if own:
                got += 0.0 if absent else value
                top += own
        if not subjects and whole and (whole["marks"] is not None or whole["absent"]) and whole["max_marks"]:
            got, top = (0.0 if whole["absent"] else whole["marks"]), whole["max_marks"]
        pct = _pct(got, top)
        out.append({
            "id": e["id"], "name": e["name"], "start_date": e["start_date"], "end_date": e["end_date"],
            "status": e["status"], "results_published": bool(e["results_published"]), "subjects": subjects,
            "whole_exam": whole if whole and not subjects else None, "remarks": remarks,
            "total": round(got, 2) if top else None, "max_total": round(top, 2) if top else None,
            "percentage": pct, "grade": grade(pct),
        })
    final = None
    if student.get("section_id"):
        data = _public(_build(db, _section(db, student["section_id"])))
        if data["parts"]:
            final = _student_view(data, student_id)
    return {"student": {k: student.get(k) for k in ("id", "full_name", "class_name", "section_name", "roll_no")},
            "exams": out, "final": final, "grading": grading.get(db)["bands"]}
