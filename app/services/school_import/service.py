"""Bulk school import (HQ): a folder of Excel files fills a whole school at once —
classes and sections, teachers, drivers and every student.

    POST /platform/organisations/{code}/bulk-import            check (dry_run=true) or upload
    POST /platform/organisations/{code}/bulk-import/siblings   add one student after the brother/sister check
    GET  /platform/bulk-import/template[?school=CODE]          the folder as a .zip (empty, or the school's data)

Owner's rules (21 Nov 2026):
* **Any problem in any file stops everything.** The check lists every problem
  (file, row, column, what to do) and the upload saves nothing until the
  folder is clean. The upload runs in ONE transaction: all or nothing.
* **Uploading again updates.** A class already in the app keeps what it has and
  gets the sheet's new sections and subjects (nothing is removed). A teacher /
  driver is matched by username (the phone when Username is empty), a student by
  admission number; their non-blank cells replace what is saved, and a student
  moves to the section of the file they are in. Nobody is removed or marked left.
* **Usernames**: empty Username = the phone number (staff) / the primary phone
  (students) as 10 digits.
* **Passwords**: never in the sheets. New teachers and drivers get an easy
  password (services/passwords.py), returned ONCE in the upload's answer
  (`logins`) for HQ to hand over. New students get NO password: nobody can
  sign in as them until the class teacher uses "New password & share"
  (services/login_share.py). Everyone must choose their own password at the
  first sign-in.
* **Brothers and sisters**: a new student whose username another student
  already has (in the app, or earlier in the same upload) is NOT added; they
  come back in `sibling_checks`, and HQ asks for each one: "brother / sister of
  …?" yes → POST …/siblings with `sibling: true`, no → with another username.
* Class teacher / subject assignments, fees, vehicles and the timetable are not
  part of the import (done in the apps).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import UploadFile

from app.core.errors import BadRequest, Conflict, DuplicateKey, NotFound, PayloadTooLarge
from app.core.security import hash_password
from app.core.validators import is_valid_email
from app.db import Db
from app.services import audit, labels, organisations, passwords, siblings, spreadsheet
from app.services.school_import import sheets
from app.services.school_import.sheets import CLASSES, DRIVERS, STUDENTS, TEACHERS

MAX_FILES = 400
MAX_FILE_MB = 2
MAX_TOTAL_MB = 18          # Caddy accepts 20 MB per request
MAX_STUDENTS = 10_000
INVALID = "import_invalid"


@dataclass
class Problem:
    file: str
    row: int | None
    column: str | None
    message: str

    def public(self) -> dict:
        return {"file": self.file, "row": self.row, "column": self.column, "message": self.message}


@dataclass
class Row:
    file: str
    number: int                      # Excel row number (header = 1)
    values: dict[str, Any] = field(default_factory=dict)  # cleaned, non-blank cells


@dataclass
class ClassPlan:
    name: str
    key: str
    existing_id: int | None = None
    has_sections: bool = True         # after the upload
    sections: list[str] = field(default_factory=list)       # every section after the upload (names)
    new_sections: list[str] = field(default_factory=list)
    split: bool = False               # an existing class without sections gets its first sections
    new_subjects: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)       # every subject after the upload


@dataclass
class Person:                          # a teacher or a driver row
    row: Row
    username: str
    existing_id: int | None = None


@dataclass
class StudentPlan:
    row: Row
    class_key: str
    section_key: str                  # folded section name ("" = class without sections)
    existing: dict | None = None      # the student with this admission number
    username: str | None = None       # new students
    check: bool = False               # new student whose username another student has
    moved: bool = False


@dataclass
class Plan:
    files: list[dict] = field(default_factory=list)
    ignored: list[dict] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    classes: dict[str, ClassPlan] = field(default_factory=dict)
    teachers: list[Person] = field(default_factory=list)
    drivers: list[Person] = field(default_factory=list)
    students: list[StudentPlan] = field(default_factory=list)

    def problem(self, file: str, row: int | None, column: str | None, message: str) -> None:
        self.problems.append(Problem(file, row, column, message))

    def warn(self, file: str, row: int | None, column: str | None, message: str) -> None:
        self.warnings.append(Problem(file, row, column, message))


# ------------------------------------------------------------- the request

def bind_school(db: Db, code: str) -> dict:
    """The school the import is for (HQ names it by code); the request is bound to it."""
    org = organisations.find(db, code)
    if org is None:
        raise NotFound("School not found")
    db.bind_org(org["id"])
    return org


def read_uploads(files: list[UploadFile], paths: str | None) -> list[tuple[str, bytes]]:
    """The uploaded files with their paths inside the folder. Browsers send only the file
    name for a folder upload, so the client sends `paths` (a JSON list, same order)."""
    if not files:
        raise BadRequest("Pick the school's folder: no files were uploaded.")
    if len(files) > MAX_FILES:
        raise BadRequest(f"At most {MAX_FILES} files in one upload.")
    names: list[str] = []
    if paths:
        try:
            names = [str(p) for p in json.loads(paths)]
        except (ValueError, TypeError):
            raise BadRequest("paths: a JSON list of the files' paths") from None
        if len(names) != len(files):
            raise BadRequest("paths: one path per file")
    out, total = [], 0
    for i, upload in enumerate(files):
        name = (names[i] if names else "") or upload.filename or f"file{i + 1}.xlsx"
        data = upload.file.read(MAX_FILE_MB * 1024 * 1024 + 1)
        if len(data) > MAX_FILE_MB * 1024 * 1024 and sheets.classify(name).kind:
            raise PayloadTooLarge(f"{name} is larger than {MAX_FILE_MB} MB. Is it the right file?")
        total += len(data)
        if total > MAX_TOTAL_MB * 1024 * 1024:
            raise PayloadTooLarge(f"The folder is larger than {MAX_TOTAL_MB} MB. Leave out photos and other files.")
        out.append((name, data))
    return out


# ----------------------------------------------------------- reading files

def _read(plan: Plan, path: str, data: bytes, kind: str) -> list[Row] | None:
    """The file's data rows, cleaned and checked cell by cell (problems recorded)."""
    try:
        table = spreadsheet.read_rows(data, what=path)
    except BadRequest as exc:
        plan.problem(path, None, None, str(exc.detail))
        return None
    header_index = next((i for i, r in enumerate(table) if any(c.strip() for c in r)), None)
    if header_index is None:
        plan.problem(path, None, None, "The file is empty.")
        return None
    found, unknown = sheets.resolve_headers(kind, table[header_index])
    cols = sheets.COLUMNS[kind]
    missing = [c.header for c in cols if c.required and c.key not in found]
    if missing:
        plan.problem(path, header_index + 1, None,
                     "Missing column" + ("s" if len(missing) > 1 else "") + ": " + ", ".join(missing)
                     + ". Keep the column names of the template in the first row.")
        return None
    if unknown:
        plan.warn(path, header_index + 1, None, "Columns not used (ignored): " + ", ".join(unknown))
    rows: list[Row] = []
    for offset, cells in enumerate(table[header_index + 1:], start=header_index + 2):
        if not any((c or "").strip() for c in cells):
            continue
        if len(rows) >= sheets.MAX_ROWS_PER_FILE:
            plan.problem(path, offset, None, f"More than {sheets.MAX_ROWS_PER_FILE} rows: split the file.")
            return None
        row = Row(path, offset)
        for col in cols:
            if col.key not in found:
                continue
            raw = cells[found[col.key]] if found[col.key] < len(cells) else ""
            raw = " ".join((raw or "").split()) if col.key not in ("address",) else (raw or "").strip()
            if not raw:
                if col.required:
                    plan.problem(path, offset, col.header, f"{col.header} is required.")
                continue
            try:
                value: Any = _clean(col, raw)
            except ValueError as exc:
                plan.problem(path, offset, col.header, f"{exc}.")
                continue
            row.values[col.key] = value
        rows.append(row)
    return rows


def _clean(col: sheets.Col, raw: str) -> Any:
    if col.kind == "phone":
        return sheets.phone(raw)
    if col.kind == "date":
        return sheets.parse_date(raw)
    if col.kind == "list":
        return sheets.split_list(raw)
    if col.kind == "email":
        if not is_valid_email(raw):
            raise ValueError("Enter a valid email address, or leave it empty")
        return raw
    if col.max_length and len(raw) > col.max_length:
        raise ValueError(f"At most {col.max_length} characters")
    if col.key == "username" and (len(raw) < 3 or any(ch.isspace() for ch in raw)):
        raise ValueError("A username needs at least 3 characters and no spaces")
    return raw


# --------------------------------------------------------------- planning

def _load(db: Db) -> dict:
    """Everything the plan compares against, in a fixed number of queries."""
    classes = db.all("SELECT id, name, has_sections FROM classes WHERE org_id = %s ORDER BY id", (db.org,))
    sections = db.all("SELECT id, class_id, name FROM sections WHERE org_id = %s ORDER BY id", (db.org,))
    subjects = db.all("SELECT cs.class_id, cs.name FROM class_subjects cs JOIN classes c ON c.id = cs.class_id "
                      "WHERE c.org_id = %s ORDER BY cs.id", (db.org,))
    users = db.all("SELECT id, username, user_type, full_name, phone, left_on FROM users WHERE org_id = %s",
                   (db.org,))
    students = db.all("SELECT sp.user_id, sp.admission_no, sp.roll_no, sp.class_id, sp.section_id, u.username, "
                      "u.full_name, u.left_on FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
                      "WHERE sp.org_id = %s", (db.org,))
    return {"classes": classes, "sections": sections, "subjects": subjects, "users": users, "students": students}


def build_plan(db: Db, uploads: list[tuple[str, bytes]]) -> Plan:
    plan = Plan()
    known = _load(db)
    by_kind: dict[str, list[tuple[str, bytes, sheets.FileName]]] = {CLASSES: [], TEACHERS: [], DRIVERS: [],
                                                                    STUDENTS: []}
    for path, data in uploads:
        name = sheets.classify(path)
        if name.kind is None:
            plan.ignored.append({"path": name.path, "reason": name.ignored_reason})
            continue
        by_kind[name.kind].append((name.path, data, name))
    for kind in (CLASSES, TEACHERS, DRIVERS):
        if len(by_kind[kind]) > 1:
            plan.problem(", ".join(p for p, _, _ in by_kind[kind]), None, None,
                         f"There is more than one {kind}.xlsx in the folder. Keep one.")
            by_kind[kind] = by_kind[kind][:1]
    if not any(by_kind.values()):
        plan.problem("(folder)", None, None, "No Excel files found. Pick the folder with classes.xlsx, "
                                             "teachers.xlsx, drivers.xlsx and the students files.")
        return plan

    _plan_classes(plan, known, by_kind[CLASSES])
    staff_names = _plan_staff(plan, known, by_kind[TEACHERS], TEACHERS)
    staff_names |= _plan_staff(plan, known, by_kind[DRIVERS], DRIVERS)
    _plan_students(plan, known, by_kind[STUDENTS], staff_names)
    return plan


def _plan_classes(plan: Plan, known: dict, files: list) -> None:
    sections_of: dict[int, list[dict]] = {}
    for s in known["sections"]:
        sections_of.setdefault(s["class_id"], []).append(s)
    subjects_of: dict[int, list[str]] = {}
    for s in known["subjects"]:
        subjects_of.setdefault(s["class_id"], []).append(s["name"])
    for c in known["classes"]:  # classes already in the app (kept as they are unless the sheet adds to them)
        names = [s["name"] for s in sections_of.get(c["id"], []) if s["name"] != ""]
        plan.classes[sheets.fold(c["name"])] = ClassPlan(
            name=c["name"], key=sheets.fold(c["name"]), existing_id=c["id"], has_sections=bool(c["has_sections"]),
            sections=names if c["has_sections"] else [], subjects=list(subjects_of.get(c["id"], [])))
    if not files:
        return
    path, data, _ = files[0]
    rows = _read(plan, path, data, CLASSES)
    plan.files.append({"path": path, "kind": CLASSES, "rows": len(rows or [])})
    if not rows:
        return
    seen: dict[str, int] = {}
    for row in rows:
        name = row.values.get("name")
        if not name:
            continue
        key = sheets.fold(name)
        if key in seen:
            plan.problem(path, row.number, "Class name", f"{name} is also on row {seen[key]}. One row per class.")
            continue
        seen[key] = row.number
        secs = row.values.get("sections") or []
        bad = [s for s in secs if len(s) > 10]
        if bad:
            plan.problem(path, row.number, "Sections", f"Section names are at most 10 characters: {', '.join(bad)}.")
            continue
        subs = [s[:100] for s in row.values.get("subjects") or []]
        cp = plan.classes.get(key)
        if cp is None:  # a new class
            plan.classes[key] = ClassPlan(name=name, key=key, has_sections=bool(secs), sections=list(secs),
                                          new_sections=list(secs), subjects=list(subs), new_subjects=list(subs))
            continue
        have = {sheets.fold(s) for s in cp.sections}
        add = [s for s in secs if sheets.fold(s) not in have]
        if secs and not cp.has_sections:
            cp.split, cp.has_sections = True, True
        if not secs and cp.has_sections and cp.sections:
            plan.warn(path, row.number, "Sections",
                      f"{labels.class_section(cp.name, None)} already has sections "
                      f"{', '.join(cp.sections)}; they are kept.")
        cp.new_sections, cp.sections = add, cp.sections + add
        have_subjects = {sheets.fold(s) for s in cp.subjects}
        cp.new_subjects = [s for s in subs if sheets.fold(s) not in have_subjects]
        cp.subjects = cp.subjects + cp.new_subjects


def _plan_staff(plan: Plan, known: dict, files: list, kind: str) -> set[str]:
    """Teachers or drivers. Returns the folded usernames they will have (students can't share them)."""
    user_type = "teacher" if kind == TEACHERS else "driver"
    if not files:
        return set()
    path, data, _ = files[0]
    rows = _read(plan, path, data, kind)
    plan.files.append({"path": path, "kind": kind, "rows": len(rows or [])})
    if not rows:
        return set()
    by_username = {sheets.fold(u["username"]): u for u in known["users"]}
    phone_owner: dict[str, dict] = {}
    for u in known["users"]:
        if u["user_type"] != "student" and u["phone"]:
            try:
                phone_owner.setdefault(sheets.phone(u["phone"]), u)
            except ValueError:
                pass
    seen: dict[str, int] = {}
    out: set[str] = set()
    people = plan.teachers if kind == TEACHERS else plan.drivers
    for row in rows:
        username = row.values.get("username") or row.values.get("phone")
        if not username or "full_name" not in row.values:
            continue  # a required-cell problem is already listed
        key = sheets.fold(username)
        col = "Username" if row.values.get("username") else "Phone"
        if key in seen:
            plan.problem(path, row.number, col, f"The username {username} is also on row {seen[key]}. "
                                                "Each person needs their own (put a different Username).")
            continue
        seen[key] = row.number
        holder = by_username.get(key)
        if holder is None:
            owner = phone_owner.get(row.values.get("phone") or "")
            if owner is not None and not row.values.get("username"):
                plan.problem(path, row.number, "Phone",
                             f"{owner['full_name']} is already in the app with this phone number (username "
                             f"{owner['username']}). To update them, put {owner['username']} in the Username "
                             "column; for someone else, put a different Username.")
                continue
            people.append(Person(row, username))
        elif holder["user_type"] != user_type:
            who = "a student's (parent's) login" if holder["user_type"] == "student" else \
                f"{holder['full_name']}'s login ({holder['user_type'].replace('_', ' ')})"
            plan.problem(path, row.number, col, f"The username {username} is already {who}. "
                                                "Put a different Username for this person.")
            continue
        elif holder["left_on"] is not None:
            plan.problem(path, row.number, col, f"{holder['full_name']} ({username}) is marked as left the school. "
                                                "Reactivate them in the app first, or leave this row out.")
            continue
        else:
            people.append(Person(row, username, existing_id=holder["id"]))
        out.add(key)
    return out


def _plan_students(plan: Plan, known: dict, files: list, staff_names: set[str]) -> None:
    by_admission = {sheets.fold(s["admission_no"]): s for s in known["students"] if s["admission_no"]}
    staff_users = {sheets.fold(u["username"]): u for u in known["users"] if u["user_type"] != "student"}
    student_names = {sheets.fold(u["username"]) for u in known["users"] if u["user_type"] == "student"}
    section_ids: dict[tuple[str, str], int] = {}
    class_by_id = {c["id"]: c for c in known["classes"]}
    for s in known["sections"]:
        c = class_by_id.get(s["class_id"])
        if c:
            section_ids[(sheets.fold(c["name"]), sheets.fold(s["name"]))] = s["id"]

    target_of: dict[str, tuple[str, str]] = {}   # file path -> (class key, section key)
    claimed: dict[tuple[str, str], str] = {}
    for path, _data, name in files:
        target = _target(plan, name)
        if target is None:
            continue
        if target in claimed:
            plan.problem(path, None, None, f"{claimed[target]} is for the same section. One file per section.")
            continue
        claimed[target] = path
        target_of[path] = target

    seen_admission: dict[str, str] = {}
    new_usernames: set[str] = set()
    total = 0
    for path, data, _ in sorted(files, key=lambda f: f[0].lower()):
        if path not in target_of:
            continue
        rows = _read(plan, path, data, STUDENTS)
        plan.files.append({"path": path, "kind": STUDENTS, "rows": len(rows or []),
                           "label": _label(plan, *target_of[path])})
        total += len(rows or [])
        for row in rows or []:
            adm = row.values.get("admission_no")
            if not adm or "full_name" not in row.values or "guardian_phone" not in row.values:
                continue
            key = sheets.fold(adm)
            if key in seen_admission:
                plan.problem(path, row.number, "Admission no.",
                             f"Admission no. {adm} is also in {seen_admission[key]}.")
                continue
            seen_admission[key] = f"{path} row {row.number}"
            sp = StudentPlan(row, *target_of[path])
            existing = by_admission.get(key)
            if existing is not None:
                if existing["left_on"] is not None:
                    plan.problem(path, row.number, "Admission no.",
                                 f"Admission no. {adm} is {existing['full_name']}, who is marked as left the school. "
                                 "Reactivate them in the app first, or leave this row out.")
                    continue
                sp.existing = existing
                given = row.values.get("username")
                if given and sheets.fold(given) != sheets.fold(existing["username"]):
                    plan.warn(path, row.number, "Username", f"{existing['full_name']} keeps the username "
                              f"{existing['username']}. Change a username on the student's page in the app.")
                plan.students.append(sp)
                continue
            username = row.values.get("username") or row.values["guardian_phone"]
            ukey = sheets.fold(username)
            if ukey in staff_names or ukey in staff_users:
                holder = staff_users.get(ukey)
                who = (f"{holder['full_name']}'s staff login" if holder
                       else "a teacher's or driver's username in this upload")
                plan.problem(path, row.number, "Username" if row.values.get("username") else "Primary phone",
                             f"{username} is {who}. Put a different Username for this student "
                             "(e.g. the other parent's mobile number).")
                continue
            sp.username = username
            sp.check = ukey in student_names or ukey in new_usernames
            new_usernames.add(ukey)
            plan.students.append(sp)
    if total > MAX_STUDENTS:
        plan.problem("(folder)", None, None, f"More than {MAX_STUDENTS} students in one upload: upload in parts.")
    _check_rolls(plan, known, section_ids)


def _target(plan: Plan, name: sheets.FileName) -> tuple[str, str] | None:
    """Which class and section a students file is for, from its name."""
    for cls, sec in sheets.student_file_candidates(name.stem):
        cp = plan.classes.get(sheets.fold(cls))
        if cp is None:
            continue
        if sec is None:
            if not cp.has_sections:
                return cp.key, ""
            continue
        if any(sheets.fold(s) == sheets.fold(sec) for s in cp.sections):
            return cp.key, sheets.fold(sec)
    known = sorted(plan.classes.values(), key=lambda c: c.name)
    examples = [sheets.student_file_name(c.name, c.sections[0] if c.has_sections and c.sections else None)
                .split("/", 1)[1] for c in known[:3]]
    plan.problem(name.path, None, None,
                 f"\"{name.stem}\" doesn't match a class and section"
                 + (" in classes.xlsx or the app" if known else " (add the class to classes.xlsx)")
                 + ". Name the file after its section, e.g. "
                 + (", ".join(f'"{e}"' for e in examples) if examples else '"Class 5 - A.xlsx"') + ".")
    return None


def _label(plan: Plan, class_key: str, section_key: str) -> str:
    cp = plan.classes[class_key]
    sec = next((s for s in cp.sections if sheets.fold(s) == section_key), None) if section_key else None
    return labels.class_section(cp.name, sec)


def _check_rolls(plan: Plan, known: dict, section_ids: dict[tuple[str, str], int]) -> None:
    """Roll numbers stay unique per section after the upload."""
    updated = {sp.existing["user_id"] for sp in plan.students if sp.existing}
    ids_to_key = {v: k for k, v in section_ids.items()}
    occupied: dict[tuple[tuple[str, str], str], str] = {}
    for s in known["students"]:  # students the upload doesn't touch keep their roll numbers
        if s["user_id"] in updated or s["left_on"] is not None or not s["roll_no"] or s["section_id"] not in ids_to_key:
            continue
        occupied[(ids_to_key[s["section_id"]], sheets.fold(s["roll_no"]))] = f"{s['full_name']} (already in the app)"
    for sp in plan.students:
        roll = sp.row.values.get("roll_no") or (sp.existing or {}).get("roll_no")
        target = (sp.class_key, sp.section_key)
        if sp.existing:
            sp.moved = section_ids.get(target) != sp.existing["section_id"]
        if not roll:
            continue
        slot = (target, sheets.fold(roll))
        if slot in occupied:
            plan.problem(sp.row.file, sp.row.number, "Roll no.", f"Roll no. {roll} is also {occupied[slot]} "
                                                                 "in this section.")
            continue
        occupied[slot] = f"{sp.row.values.get('full_name')} ({sp.row.file} row {sp.row.number})"


# ----------------------------------------------------------------- report

def _student_public(plan: Plan, sp: StudentPlan) -> dict:
    v = sp.row.values
    cp = plan.classes[sp.class_key]
    section = next((s for s in cp.sections if sheets.fold(s) == sp.section_key), "") if sp.section_key else ""
    return {
        "full_name": v.get("full_name"), "admission_no": v.get("admission_no"), "roll_no": v.get("roll_no"),
        "dob": v["dob"].isoformat() if v.get("dob") else None, "father_name": v.get("father_name"),
        "mother_name": v.get("mother_name"), "guardian_phone": v.get("guardian_phone"), "email": v.get("email"),
        "phone": v.get("phone"), "address": v.get("address"), "username": sp.username,
        "class_name": cp.name, "section_name": section, "class_label": labels.class_section(cp.name, section or None),
        "file": sp.row.file, "row": sp.row.number,
    }


def report(plan: Plan) -> dict:
    classes = list(plan.classes.values())
    new_students = [s for s in plan.students if not s.existing]
    return {
        "ok": not plan.problems,
        "problems": [p.public() for p in plan.problems],
        "warnings": [p.public() for p in plan.warnings],
        "files": plan.files,
        "ignored": plan.ignored,
        "summary": {
            "classes_new": sum(1 for c in classes if c.existing_id is None),
            "classes_updated": sum(1 for c in classes if c.existing_id and (c.new_sections or c.new_subjects)),
            "sections_new": sum(len(c.new_sections) for c in classes),
            "subjects_new": sum(len(c.new_subjects) for c in classes),
            "teachers_new": sum(1 for p in plan.teachers if not p.existing_id),
            "teachers_updated": sum(1 for p in plan.teachers if p.existing_id),
            "drivers_new": sum(1 for p in plan.drivers if not p.existing_id),
            "drivers_updated": sum(1 for p in plan.drivers if p.existing_id),
            "students_new": sum(1 for s in new_students if not s.check),
            "students_updated": sum(1 for s in plan.students if s.existing),
            "students_moved": sum(1 for s in plan.students if s.existing and s.moved),
            "sibling_checks": sum(1 for s in new_students if s.check),
        },
        "classes": [{
            "name": c.name, "label": labels.class_section(c.name, None), "new": c.existing_id is None,
            "sections": c.sections, "new_sections": c.new_sections, "new_subjects": c.new_subjects,
            "students": sum(1 for s in plan.students if s.class_key == c.key),
        } for c in sorted(classes, key=lambda c: c.name) if c.existing_id is None or c.new_sections
            or c.new_subjects or any(s.class_key == c.key for s in plan.students)],
        "sibling_checks": [_student_public(plan, s) for s in new_students if s.check],
    }


# ------------------------------------------------------------------ check / upload

def check(db: Db, uploads: list[tuple[str, bytes]]) -> dict:
    """What the upload would do; nothing is saved."""
    return report(build_plan(db, uploads))


def apply(db: Db, uploads: list[tuple[str, bytes]], operator: str | None) -> dict:
    """Save everything in one transaction, or nothing (400 `import_invalid` with the report)."""
    with db.transaction():
        siblings.lock_school(db)  # first statement: what the plan reads is current (REPEATABLE READ)
        plan = build_plan(db, uploads)
        result = report(plan)
        if plan.problems:
            raise BadRequest(f"Nothing was saved: {len(plan.problems)} problem"
                             f"{'s' if len(plan.problems) != 1 else ''} to fix in the files.",
                             extra={"code": INVALID, "report": result})
        _save_classes(db, plan)
        section_ids = _section_ids(db)
        logins = _save_staff(db, plan.teachers, "teacher") + _save_staff(db, plan.drivers, "driver")
        checks = _save_students(db, plan, section_ids)
        audit.record(db, None, "school_import.apply", "organisation", db.org, {
            **result["summary"], "files": len(plan.files), **({"operator": operator} if operator else {})})
    result["saved"] = True
    result["logins"] = logins
    result["sibling_checks"] = checks
    return result


def _save_classes(db: Db, plan: Plan) -> None:
    for cp in plan.classes.values():
        if cp.existing_id is None:
            class_id = db.execute(
                "INSERT INTO classes (org_id, name, fee_amount, has_sections) VALUES (%s, %s, NULL, %s)",
                (db.org, cp.name, 1 if cp.has_sections else 0)).lastrowid
            names = cp.new_sections if cp.has_sections else [""]
        else:
            class_id, names = cp.existing_id, list(cp.new_sections)
            if cp.split and names:  # the class's one hidden section becomes its first named section (0021)
                hidden = db.one("SELECT id FROM sections WHERE class_id = %s AND name = '' FOR UPDATE", (class_id,))
                db.execute("UPDATE classes SET has_sections = 1 WHERE id = %s", (class_id,))
                if hidden:
                    db.execute("UPDATE sections SET name = %s WHERE id = %s", (names[0], hidden["id"]))
                    names = names[1:]
        db.execute_many("INSERT INTO sections (org_id, class_id, name) VALUES (%s, %s, %s)",
                        [(db.org, class_id, n) for n in names])
        db.execute_many("INSERT INTO class_subjects (class_id, name) VALUES (%s, %s)",
                        [(class_id, n) for n in cp.new_subjects])


def _section_ids(db: Db) -> dict[tuple[str, str], int]:
    return {(sheets.fold(r["class_name"]), sheets.fold(r["name"])): r["id"] for r in db.all(
        "SELECT s.id, s.name, c.name AS class_name FROM sections s JOIN classes c ON c.id = s.class_id "
        "WHERE s.org_id = %s", (db.org,))}


def _save_staff(db: Db, people: list[Person], user_type: str) -> list[dict]:
    logins = []
    for p in people:
        v = p.row.values
        if p.existing_id is None:
            password = passwords.easy()
            try:
                user_id = db.execute(
                    "INSERT INTO users (org_id, username, password_hash, full_name, email, phone, user_type, "
                    "must_change_password) VALUES (%s, %s, %s, %s, %s, %s, %s, 1)",
                    (db.org, p.username, hash_password(password), v["full_name"], v.get("email"), v.get("phone"),
                     user_type)).lastrowid
            except DuplicateKey:
                raise Conflict(f"The username {p.username} was taken while saving. Nothing was saved; "
                               "check the folder again.") from None
            logins.append({"user_type": user_type, "id": user_id, "full_name": v["full_name"],
                           "username": p.username, "password": password, "phone": v.get("phone")})
        else:
            user_id = p.existing_id
            changes = {k: v[k] for k in ("full_name", "email", "phone") if k in v}
            if changes:
                db.execute(f"UPDATE users SET {', '.join(f'{k} = %s' for k in changes)} WHERE id = %s",
                           (*changes.values(), user_id))
        if user_type == "teacher":
            info = {k: v[k] for k in ("designation", "subject", "qualification", "joining_date", "address") if k in v}
            if info or p.existing_id is None:
                cols = ["user_id", *info]
                db.execute(
                    f"INSERT INTO teacher_profiles ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                    + ("ON DUPLICATE KEY UPDATE " + ", ".join(f"{k} = VALUES({k})" for k in info) if info
                       else "ON DUPLICATE KEY UPDATE user_id = user_id"),
                    (user_id, *info.values()))
        elif "licence_no" in v:
            db.execute("INSERT INTO driver_profiles (user_id, licence_no) VALUES (%s, %s) "
                       "ON DUPLICATE KEY UPDATE licence_no = VALUES(licence_no)", (user_id, v["licence_no"]))
    return logins


_PROFILE = ("roll_no", "admission_no", "dob", "father_name", "mother_name", "guardian_phone", "address")


def _save_students(db: Db, plan: Plan, section_ids: dict[tuple[str, str], int]) -> list[dict]:
    class_ids = {sheets.fold(r["name"]): r["id"] for r in
                 db.all("SELECT id, name FROM classes WHERE org_id = %s", (db.org,))}
    # roll numbers first: a student taking another one's roll (swaps, moves) must not hit the unique key midway
    moving = [sp.existing["user_id"] for sp in plan.students if sp.existing
              and (sp.moved or ("roll_no" in sp.row.values
                                and sp.row.values["roll_no"] != sp.existing["roll_no"]))]
    if moving:
        db.execute(f"UPDATE student_profiles SET roll_no = NULL WHERE user_id IN ({', '.join(['%s'] * len(moving))})",
                   tuple(moving))
    no_password = passwords.unusable_hash()
    checks = []
    for sp in plan.students:
        v = sp.row.values
        class_id, section_id = class_ids[sp.class_key], section_ids[(sp.class_key, sp.section_key)]
        if sp.existing:
            user_id = sp.existing["user_id"]
            changes = {k: v[k] for k in ("full_name", "email", "phone") if k in v}
            if changes:
                db.execute(f"UPDATE users SET {', '.join(f'{k} = %s' for k in changes)} WHERE id = %s",
                           (*changes.values(), user_id))
            profile = {k: v[k] for k in _PROFILE if k in v and k != "admission_no"}
            if sp.moved or "roll_no" not in profile:
                profile.setdefault("roll_no", sp.existing["roll_no"])
            profile.update(class_id=class_id, section_id=section_id)
            db.execute(f"UPDATE student_profiles SET {', '.join(f'{k} = %s' for k in profile)} WHERE user_id = %s",
                       (*profile.values(), user_id))
            continue
        if sp.check:
            checks.append(_student_public(plan, sp))
            continue
        _insert_student(db, sp.username, no_password, v, class_id, section_id)
    for item in checks:  # who already has the username (incl. students added just above)
        holders = db.all(
            "SELECT u.id, u.full_name, sp.admission_no, sp.father_name, c.name AS class_name, s.name AS section_name "
            "FROM users u LEFT JOIN student_profiles sp ON sp.user_id = u.id "
            "LEFT JOIN classes c ON c.id = sp.class_id LEFT JOIN sections s ON s.id = sp.section_id "
            "WHERE u.org_id = %s AND u.username = %s AND u.user_type = 'student' AND u.left_on IS NULL "
            "ORDER BY u.id LIMIT 20", (db.org, item["username"]))
        item["matches"] = [{**h, "class_label": labels.class_section(h["class_name"], h["section_name"])}
                           for h in holders]
    return checks


def _insert_student(db: Db, username: str, password_hash: str, v: dict, class_id: int, section_id: int) -> int:
    user_id = db.execute(
        "INSERT INTO users (org_id, username, password_hash, full_name, email, phone, user_type, must_change_password) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'student', 1)",
        (db.org, username, password_hash, v["full_name"], v.get("email"), v.get("phone"))).lastrowid
    db.execute(
        "INSERT INTO student_profiles (org_id, user_id, class_id, section_id, roll_no, admission_no, dob, "
        "father_name, mother_name, guardian_phone, address) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (db.org, user_id, class_id, section_id, *(v.get(k) for k in _PROFILE)))
    return user_id


# ------------------------------------------------- brother / sister check

def add_checked_student(db: Db, student: dict, sibling: bool, username: str | None,
                        operator: str | None) -> dict:
    """After the upload: add one student from `sibling_checks`. `sibling: true` = a
    brother / sister of the students who have the username; else `username` = the
    new username to give them (it must be free: 409 `username_shared` otherwise)."""
    with db.transaction():
        siblings.lock_school(db)
        row = Row("(check)", 0)
        for col in sheets.STUDENT_COLUMNS:
            raw = student.get(col.key)
            if raw is None or str(raw).strip() == "":
                if col.required:
                    raise BadRequest(f"{col.header} is required.")
                continue
            try:
                row.values[col.key] = _clean(col, " ".join(str(raw).split()))
            except ValueError as exc:
                raise BadRequest(f"{col.header}: {exc}.") from None
        name = (username or "").strip() if not sibling else (student.get("username") or row.values["guardian_phone"])
        if not name or len(name) < 3 or any(ch.isspace() for ch in name):
            raise BadRequest("Username: at least 3 characters, no spaces")
        cls = db.one("SELECT id, name, has_sections FROM classes WHERE org_id = %s AND name = %s",
                     (db.org, student.get("class_name") or ""))
        section = cls and db.one("SELECT id, name FROM sections WHERE class_id = %s AND name = %s",
                                 (cls["id"], (student.get("section_name") or "") if cls["has_sections"] else ""))
        if not section:
            raise BadRequest("The student's class / section is not in the app any more.")
        if db.one("SELECT user_id FROM student_profiles WHERE org_id = %s AND admission_no = %s",
                  (db.org, row.values["admission_no"])):
            raise Conflict(f"Admission no. {row.values['admission_no']} is already in the app (added already?).")
        if row.values.get("roll_no") and db.one(
                "SELECT user_id FROM student_profiles WHERE section_id = %s AND roll_no = %s",
                (section["id"], row.values["roll_no"])):
            raise Conflict(f"Roll no. {row.values['roll_no']} is already taken in this section.")
        shared = siblings.check_student_username(db, name, sibling=sibling)
        if sibling and not shared:
            raise Conflict(f"Nobody has the username {name} any more: add the student with sibling false.")
        user_id = _insert_student(db, name, passwords.unusable_hash(), row.values, cls["id"], section["id"])
        audit.record(db, None, "school_import.student_added", "user", user_id, {
            "username": name, "sibling": sibling, "siblings": [s["id"] for s in shared],
            **({"operator": operator} if operator else {})})
    return {"id": user_id, "username": name, "full_name": row.values["full_name"],
            "class_label": labels.class_section(cls["name"], section["name"] or None),
            "siblings": shared}


# --------------------------------------------------------------- template

def current_data(db: Db) -> dict:
    """The school's classes, teachers, drivers and students as template rows."""
    classes = db.all("SELECT id, name, has_sections FROM classes WHERE org_id = %s ORDER BY name", (db.org,))
    secs = db.all("SELECT id, class_id, name FROM sections WHERE org_id = %s ORDER BY name", (db.org,))
    subs = db.all("SELECT cs.class_id, cs.name FROM class_subjects cs JOIN classes c ON c.id = cs.class_id "
                  "WHERE c.org_id = %s ORDER BY cs.id", (db.org,))
    out: dict = {CLASSES: [], TEACHERS: [], DRIVERS: [], STUDENTS: {}}
    for c in classes:
        out[CLASSES].append([c["name"], ", ".join(s["name"] for s in secs if s["class_id"] == c["id"] and s["name"]),
                             ", ".join(s["name"] for s in subs if s["class_id"] == c["id"])])
    for t in db.all("SELECT u.full_name, u.phone, u.username, u.email, tp.designation, tp.subject, tp.qualification, "
                    "tp.joining_date, tp.address FROM users u LEFT JOIN teacher_profiles tp ON tp.user_id = u.id "
                    "WHERE u.org_id = %s AND u.user_type = 'teacher' AND u.left_on IS NULL ORDER BY u.full_name",
                    (db.org,)):
        out[TEACHERS].append([t["full_name"], t["phone"], t["username"], t["email"], t["designation"], t["subject"],
                              t["qualification"], t["joining_date"].isoformat() if t["joining_date"] else None,
                              t["address"]])
    for d in db.all("SELECT u.full_name, u.phone, u.username, u.email, dp.licence_no FROM users u "
                    "LEFT JOIN driver_profiles dp ON dp.user_id = u.id WHERE u.org_id = %s AND u.user_type = 'driver' "
                    "AND u.left_on IS NULL ORDER BY u.full_name", (db.org,)):
        out[DRIVERS].append([d["full_name"], d["phone"], d["username"], d["email"], d["licence_no"]])
    class_name = {c["id"]: c["name"] for c in classes}
    for s in secs:
        out[STUDENTS][sheets.student_file_name(class_name.get(s["class_id"], "?"), s["name"] or None)] = []
    for st in db.all(
            "SELECT u.full_name, u.username, u.email, u.phone, sp.admission_no, sp.roll_no, sp.dob, sp.father_name, "
            "sp.mother_name, sp.guardian_phone, sp.address, sp.class_id, s.name AS section_name "
            "FROM users u JOIN student_profiles sp ON sp.user_id = u.id JOIN sections s ON s.id = sp.section_id "
            "WHERE u.org_id = %s AND u.user_type = 'student' AND u.left_on IS NULL "
            "ORDER BY CAST(sp.roll_no AS UNSIGNED), u.full_name", (db.org,)):
        key = sheets.student_file_name(class_name.get(st["class_id"], "?"), st["section_name"] or None)
        out[STUDENTS].setdefault(key, []).append([
            st["full_name"], st["admission_no"], st["roll_no"], st["dob"].isoformat() if st["dob"] else None,
            st["father_name"], st["mother_name"], st["guardian_phone"], st["username"], st["email"], st["phone"],
            st["address"]])
    return out
