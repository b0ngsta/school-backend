"""Turning a parsed CSV into a plan: which rows create a user, which update
one, which change nothing, and which have problems (with a message that
says how to fix them).

Rows with problems are skipped; every other row is saved. Rules that link
rows are written so that skipping is always safe:
* a username, admission number or roll number given on several rows flags
  all of those rows (the file is ambiguous; nothing guesses which one the
  user meant), and so does more than one new principal;
* "is this number free?" is decided only among the rows that will be saved,
  repeatedly until nothing changes: a skipped student keeps their current
  number, which can in turn make another row invalid.

The same plan is built twice on save: once to validate, and again inside
the saving transaction with the affected rows locked, so nothing that
changed in between (another import, someone editing a student) slips
through.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.clock import school_today
from app.core.validators import INVALID_EMAIL, is_valid_email
from app.db import Db
from app.services.user_import.columns import Column
from app.services.user_import.csv_io import ParsedCsv
from app.services.user_import.photos import PhotoArchive

CREATE, UPDATE, UNCHANGED, ERROR = "create", "update", "unchanged", "error"
_CHUNK = 500

_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-.]*$")
_EXCEL_NUMBER_RE = re.compile(r"^\d(\.\d+)?[eE]\+?\d+$")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d")

# what the CSV columns are called in the query below, per profile table
_EXISTING_ALIASES = {
    "student_profiles": {"address": "sp_address"},
    "teacher_profiles": {"address": "tp_address"},
}


def fold(value: str) -> str:
    """Case- and accent-insensitive key, matching how MySQL compares
    usernames and student numbers (utf8mb4_unicode_ci)."""
    decomposed = unicodedata.normalize("NFKD", value.strip())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


@dataclass
class RowPlan:
    number: int
    username: str
    action: str = ERROR
    values: dict[str, Any] = field(default_factory=dict)   # cleaned, non-blank cells
    invalid: set[str] = field(default_factory=set)         # columns whose cell was rejected
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    existing: dict | None = None
    user_id: int | None = None

    def final(self, name: str, table: str | None = None) -> Any:
        """The value after saving: the CSV's if given, else the current one."""
        if name in self.values:
            return self.values[name]
        if self.existing is None:
            return None
        return self.existing.get(_EXISTING_ALIASES.get(table or "", {}).get(name, name))

    def report(self) -> dict:
        return {
            "row": self.number,
            "username": self.username,
            "full_name": self.values.get("full_name") or (self.existing or {}).get("full_name"),
            "action": ERROR if self.errors else self.action,
            "user_id": self.user_id,
            "changes": self.changes,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class Plan:
    user_type: str
    section: dict | None
    columns: list[Column]
    rows: list[RowPlan]
    warnings: list[str]

    @property
    def has_errors(self) -> bool:
        return any(r.errors for r in self.rows)

    def rows_with(self, action: str) -> list[RowPlan]:
        return [r for r in self.rows if not r.errors and r.action == action]

    def signature(self) -> list[tuple]:
        return [(r.number, r.action, r.user_id, tuple(r.changes), tuple(r.errors)) for r in self.rows]

    def savable(self) -> list[RowPlan]:
        """Rows that will be written (new or changed users without problems)."""
        return [r for r in self.rows if not r.errors and r.action in (CREATE, UPDATE)]

    def summary(self) -> dict:
        counts = {CREATE: 0, UPDATE: 0, UNCHANGED: 0, ERROR: 0}
        for r in self.rows:
            counts[ERROR if r.errors else r.action] += 1
        return {"rows": len(self.rows), **counts,
                "warnings": len(self.warnings) + sum(bool(r.warnings) for r in self.rows)}


# ======================================================================

def build(db: Db, user_type: str, section: dict | None, parsed: ParsedCsv,
          photos: PhotoArchive | None, *, lock: bool, photos_checked: bool = False) -> Plan:
    """`lock`: take row locks (inside the saving transaction).
    `photos_checked`: the photos were already validated by an earlier build."""
    plan = Plan(user_type=user_type, section=section, columns=parsed.columns, rows=[],
                warnings=list(parsed.warnings))
    has_photo_column = any(c.name == "photo" for c in parsed.columns)

    for csv_row in parsed.rows:
        row = RowPlan(number=csv_row.number, username=csv_row.cells.get("username", ""))
        row.errors.extend(csv_row.problems)
        for column in parsed.columns:
            _clean_cell(row, column, csv_row.cells.get(column.name, ""))
        plan.rows.append(row)

    _mark_duplicate_usernames(plan.rows)
    existing = _existing_users(db, [r.username for r in plan.rows if r.username], lock)
    for row in plan.rows:
        _decide(row, existing.get(fold(row.username)) if row.username else None, plan)
    ignored = sum(row.values.pop("password", None) is not None for row in plan.rows if row.existing)
    if ignored:
        plan.warnings.append(f"Passwords were ignored for {ignored} existing user{'s' if ignored > 1 else ''}: "
                             "existing accounts keep their password (they can use 'Forgot password').")

    if user_type == "principal":
        _one_principal(db, plan, lock)
    if user_type == "student":
        _unique_student_numbers(db, plan, lock)
    if has_photo_column:
        if not photos_checked:
            _check_photos(plan, photos)
    elif photos is not None:
        plan.warnings.append("A photos ZIP was uploaded but the CSV has no 'photo' column, so no photos were used.")
    return plan


# ----------------------------------------------------------- cell checks

def _clean_cell(row: RowPlan, column: Column, raw: str) -> None:
    if not raw:
        return
    label = column.name
    errors_before = len(row.errors)
    _check_cell(row, column, raw, label)
    if len(row.errors) > errors_before:
        row.invalid.add(label)


def _check_cell(row: RowPlan, column: Column, raw: str, label: str) -> None:
    if column.kind == "date":
        parsed = _parse_date(raw)
        today = school_today()
        latest = today if label == "dob" else today.replace(year=today.year + 1, day=min(today.day, 28))
        if parsed is None:
            row.errors.append(f"{label}: '{raw}' is not a date; use YYYY-MM-DD or DD-MM-YYYY.")
        elif parsed > latest or parsed.year < 1900:
            row.errors.append(f"{label}: {raw} is not a plausible date.")
        else:
            row.values[label] = parsed
        return
    if column.max_length and len(raw) > column.max_length:
        row.errors.append(f"{label} is too long ({len(raw)} characters, max {column.max_length}).")
        return
    if column.kind == "email" and not is_valid_email(raw):
        row.errors.append(f"{label}: '{raw}' — {INVALID_EMAIL.lower()}.")
        return
    if column.kind == "phone":
        if _EXCEL_NUMBER_RE.match(raw):
            row.errors.append(f"{label}: '{raw}' was changed by Excel into a number. Format the column as "
                              "Text and type the number again.")
            return
        if not _PHONE_RE.match(raw) or sum(ch.isdigit() for ch in raw) < 6:
            row.errors.append(f"{label}: '{raw}' is not a phone number (digits, spaces, + - ( ) only).")
            return
    row.values[label] = raw


def _parse_date(raw: str) -> date | None:
    text = raw.strip().split(" ")[0]  # "2014-05-21 00:00:00" from some spreadsheets
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def rows_phrase(numbers: list[int]) -> str:
    """[2, 5, 9] -> 'rows 2, 5 and 9'."""
    text = [str(n) for n in sorted(numbers)]
    return "rows " + (", ".join(text[:-1]) + " and " + text[-1] if len(text) > 1 else text[0])


def _mark_duplicate_usernames(rows: list[RowPlan]) -> None:
    groups: dict[str, list[RowPlan]] = {}
    for row in rows:
        if not row.username:
            row.errors.append("username is required.")
            continue
        groups.setdefault(fold(row.username), []).append(row)
    for group in groups.values():
        if len(group) > 1:
            where = rows_phrase([r.number for r in group])
            for row in group:
                row.errors.append(f"Username '{row.username}' is on {where}; keep it on one row only.")


# ----------------------------------------------------- existing accounts

EXISTING_SQL = (
    "SELECT u.id, u.username, u.user_type, u.full_name, u.email, u.phone, u.photo_path, u.left_on, "
    "sp.user_id AS sp_user_id, sp.class_id, sp.section_id, sp.roll_no, sp.admission_no, sp.dob, "
    "sp.father_name, sp.mother_name, sp.guardian_phone, sp.address AS sp_address, "
    "c.name AS class_name, s.name AS section_name, "
    "tp.user_id AS tp_user_id, tp.subject, tp.qualification, tp.joining_date, tp.address AS tp_address "
    "FROM users u "
    "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
    "LEFT JOIN classes c ON c.id = sp.class_id "
    "LEFT JOIN sections s ON s.id = sp.section_id "
    "LEFT JOIN teacher_profiles tp ON tp.user_id = u.id "
)


def _existing_users(db: Db, usernames: list[str], lock: bool) -> dict[str, dict]:
    found: dict[str, dict] = {}
    unique = sorted(set(usernames), key=fold)  # stable lock order
    for i in range(0, len(unique), _CHUNK):
        part = unique[i:i + _CHUNK]
        sql = EXISTING_SQL + f"WHERE u.org_id = %s AND u.username IN ({', '.join(['%s'] * len(part))})"
        for user in db.all(sql + (" FOR UPDATE" if lock else ""), [db.org, *part]):
            found[fold(user["username"])] = user
    return found


def place(class_name: str | None, section_name: str | None) -> str:
    if class_name and section_name:
        return f"{class_name}-{section_name}"
    return class_name or "no section"


def _decide(row: RowPlan, user: dict | None, plan: Plan) -> None:
    if not row.username:
        return
    if user is None:
        _decide_create(row)
        return
    row.existing, row.user_id = user, user["id"]
    if user["user_type"] != plan.user_type:
        kind = user["user_type"].replace("_", "-")
        row.errors.append(f"Username '{row.username}' is already used by a {kind} account.")
        return
    if user.get("left_on"):
        row.errors.append(f"{user['full_name']} is marked as left. Reactivate the account first, then upload again.")
        return
    if plan.user_type == "student":
        target = plan.section
        if user["section_id"] != target["section_id"]:
            if user["section_id"] is None:
                row.errors.append("This student already exists but is not in any section. Ask the office to "
                                  "assign the student, then upload again.")
            else:
                row.errors.append(
                    f"{user['full_name']} is already enrolled in {place(user['class_name'], user['section_name'])}. "
                    "Contact that class teacher (or the office) to move the student, or remove this row."
                )
            return

    if not row.final("email") and "email" not in row.invalid:
        row.errors.append("email is required (this account has no email yet).")

    for column in plan.columns:
        if column.name in ("username", "password") or column.name not in row.values:
            continue
        if column.kind == "photo":
            row.changes.append("photo")
            continue
        current = row.existing.get(_EXISTING_ALIASES.get(column.table or "", {}).get(column.name, column.name))
        if not _same(current, row.values[column.name]):
            row.changes.append(column.name)
    row.action = UPDATE if row.changes else UNCHANGED


def _decide_create(row: RowPlan) -> None:
    if any(ch.isspace() for ch in row.username):
        row.errors.append("Usernames cannot contain spaces.")
    if len(row.username) < 3:
        row.errors.append("username must be at least 3 characters.")
    missing = [name for name in ("full_name", "email", "password")
               if name not in row.values and name not in row.invalid]
    if missing:
        row.errors.append(f"New user: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required.")
    if 0 < len(row.values.get("password", "")) < 6:
        row.errors.append("password must be at least 6 characters.")
    row.action = CREATE


def _same(current: Any, new: Any) -> bool:
    if isinstance(new, date):
        return current == new
    if current is None:
        return False
    return str(current).strip() == new


# ------------------------------------------------------- cross-row rules

def _one_principal(db: Db, plan: Plan, lock: bool) -> None:
    current = db.one("SELECT id, username FROM users WHERE org_id = %s AND user_type = 'principal' LIMIT 1"
                     + (" FOR UPDATE" if lock else ""), (db.org,))
    creates = [r for r in plan.rows if r.action == CREATE]  # with or without other problems
    for row in creates:
        if current is not None:
            row.errors.append(f"There is already a principal ({current['username']}). Only one principal "
                              "is allowed.")
        elif len(creates) > 1:
            row.errors.append(f"Only one principal is allowed, but {rows_phrase([r.number for r in creates])} "
                              "each add one; keep one.")


def _unique_student_numbers(db: Db, plan: Plan, lock: bool) -> None:
    """Admission numbers are unique in the school, roll numbers in the
    section, after the file is applied (so swapping two students' roll
    numbers in one file is fine)."""
    section_id = plan.section["section_id"]
    suffix = " FOR UPDATE" if lock else ""
    numbers = (("admission_no", "Admission number"), ("roll_no", "Roll number"))

    # 1. the same number typed on several rows: all of them are ambiguous
    for name, label in numbers:
        groups: dict[str, list[RowPlan]] = {}
        for row in plan.rows:
            if row.values.get(name):
                groups.setdefault(fold(str(row.values[name])), []).append(row)
        for group in groups.values():
            if len(group) > 1:
                where = rows_phrase([r.number for r in group])
                for row in group:
                    row.errors.append(f"{label} {row.values[name]} is on {where}; keep it on one row only.")

    # 2. numbers held by students who are not saved by this file
    candidates = [r for r in plan.rows if not r.errors and r.action in (CREATE, UPDATE, UNCHANGED)]
    admission_values = sorted({str(r.final("admission_no")) for r in candidates if r.final("admission_no")})
    admission_holders: list[dict] = []
    for i in range(0, len(admission_values), _CHUNK):
        part = admission_values[i:i + _CHUNK]
        admission_holders += db.all(
            "SELECT sp.user_id, sp.admission_no AS value, u.full_name, c.name AS class_name, s.name AS section_name "
            "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
            "LEFT JOIN classes c ON c.id = sp.class_id LEFT JOIN sections s ON s.id = sp.section_id "
            f"WHERE sp.org_id = %s AND sp.admission_no IN ({', '.join(['%s'] * len(part))}){suffix}",
            [db.org, *part],
        )
    roll_holders = db.all(
        "SELECT sp.user_id, sp.roll_no AS value, u.full_name, NULL AS class_name, NULL AS section_name "
        "FROM student_profiles sp JOIN users u ON u.id = sp.user_id "
        f"WHERE sp.org_id = %s AND sp.section_id = %s AND sp.roll_no IS NOT NULL{suffix}",
        (db.org, section_id),
    )
    holders = {"admission_no": _by_value(admission_holders), "roll_no": _by_value(roll_holders)}

    # skipping a row keeps that student's current number, which may block
    # another row: repeat until no new problem appears
    while True:
        saved = [r for r in plan.rows if not r.errors and r.action in (CREATE, UPDATE, UNCHANGED)]
        found = False
        for name, label in numbers:
            found |= _check_holders(saved, name, label, holders[name], in_section=name == "roll_no")
        if not found:
            return


def _by_value(holders: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for holder in holders:
        grouped.setdefault(fold(str(holder["value"])), []).append(holder)
    return grouped


def _check_holders(rows: list[RowPlan], name: str, label: str, holders: dict[str, list[dict]], *,
                   in_section: bool) -> bool:
    """Adds an error to rows whose final number belongs to a student who
    keeps it. Returns whether any error was added."""
    final = {r.number: fold(str(r.final(name))) for r in rows if r.final(name)}
    staying = {r.user_id: final.get(r.number) for r in rows if r.user_id is not None}
    found = False
    for row in rows:
        key = final.get(row.number)
        for holder in holders.get(key, ()) if key else ():
            if holder["user_id"] == row.user_id:
                continue
            if holder["user_id"] in staying and staying[holder["user_id"]] != key:
                continue  # that student is saved with a different number
            where = "in this section" if in_section else f"in {place(holder['class_name'], holder['section_name'])}"
            row.errors.append(f"{label} {row.final(name)} already belongs to {holder['full_name']} ({where}).")
            found = True
            break
    return found


def _check_photos(plan: Plan, photos: PhotoArchive | None) -> None:
    for row in plan.rows:
        name = row.values.get("photo")
        if not name:
            continue
        if photos is None:
            row.errors.append(f"Photo '{name}' is listed but no photos ZIP was uploaded.")
            continue
        problem = photos.problem(name)
        if problem:
            row.errors.append(problem)
    if photos is not None:
        unused = photos.unused()
        if unused:
            shown = ", ".join(unused[:10]) + (f" and {len(unused) - 10} more" if len(unused) > 10 else "")
            plan.warnings.append(f"{len(unused)} photo(s) in the ZIP are not used by any row: {shown}.")
