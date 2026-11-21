"""The Excel sheets of the bulk school import: their columns, how a header or a
cell is read, how a file name says which class and section it is for, and the
templates (an empty set, or the school's current data to edit and upload again).

The folder:

    classes.xlsx               Class name*, Sections, Subjects*
    teachers.xlsx              Full name*, Phone*, Username, Email, Designation, Main subject, ...
    drivers.xlsx               Full name*, Phone*, Username, Email, Licence no.
    students/Class 5 - A.xlsx  one file per section; "LKG.xlsx" = a class without sections

Headers are matched without caring about case, spaces, dots, stars or
apostrophes ("Father's name" = "fathers name" = "FATHER_NAME"), and a few
other spellings are accepted (`aliases`). Only the first sheet of a file is read.
"""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime

from app.core.validators import EMAIL_MAX_LENGTH
from app.services import spreadsheet

CLASSES, TEACHERS, DRIVERS, STUDENTS = "classes", "teachers", "drivers", "students"
MAX_ROWS_PER_FILE = 2000


@dataclass(frozen=True)
class Col:
    key: str                 # name used in code
    header: str              # header in the template
    required: bool = False
    max_length: int | None = None
    kind: str = "text"       # text | phone | email | date | list
    aliases: tuple[str, ...] = ()
    example: str = ""
    note: str = ""
    width: float = 18


CLASS_COLUMNS = (
    Col("name", "Class name", True, 50, aliases=("class", "classname"), example="5",
        note='The class as it is written in the school, e.g. "5", "LKG", "Nursery". Shown as "Class 5".'),
    Col("sections", "Sections", kind="list", aliases=("section",), example="A, B, C",
        note="Section names separated by commas. Leave empty for a class that is not split into sections."),
    Col("subjects", "Subjects", True, kind="list", aliases=("subject",), example="English, Hindi, Maths, EVS",
        note="Subjects taught in this class, separated by commas.", width=40),
)

TEACHER_COLUMNS = (
    Col("full_name", "Full name", True, 100, aliases=("name", "teachername"), example="Ravi Kumar"),
    Col("phone", "Phone", True, 20, "phone", aliases=("mobile", "mobileno", "phoneno", "contact"),
        example="9876543210", note="10-digit mobile number. It is also the username when Username is empty."),
    Col("username", "Username", False, 50, aliases=("login", "loginname", "user"),
        note="Leave empty: the phone number is used. To update a teacher who is already in the app, "
             "keep their username here."),
    Col("email", "Email", False, EMAIL_MAX_LENGTH, "email", aliases=("emailaddress", "mail")),
    Col("designation", "Designation", False, 50, example="TGT"),
    Col("subject", "Main subject", False, 100, aliases=("subject", "mainsubject"), example="Maths"),
    Col("qualification", "Qualification", False, 200, example="M.Sc, B.Ed"),
    Col("joining_date", "Date of joining", False, kind="date", aliases=("joiningdate", "doj", "dateofjoining"),
        example="2021-06-01"),
    Col("address", "Address", False, 255, width=30),
)

DRIVER_COLUMNS = (
    Col("full_name", "Full name", True, 100, aliases=("name", "drivername"), example="Mohan Lal"),
    Col("phone", "Phone", True, 20, "phone", aliases=("mobile", "mobileno", "phoneno", "contact"),
        example="9812345678", note="10-digit mobile number. It is also the username when Username is empty."),
    Col("username", "Username", False, 50, aliases=("login", "loginname", "user"),
        note="Leave empty: the phone number is used."),
    Col("email", "Email", False, EMAIL_MAX_LENGTH, "email", aliases=("emailaddress", "mail")),
    Col("licence_no", "Licence no.", False, 30, aliases=("licence", "license", "licenseno", "licencenumber",
                                                          "licensenumber", "dl", "dlno", "drivinglicence"),
        example="DL0420110012345"),
)

STUDENT_COLUMNS = (
    Col("full_name", "Full name", True, 100, aliases=("name", "studentname"), example="Rahul Kumar"),
    Col("admission_no", "Admission no.", True, 30, aliases=("admission", "admissionnumber", "admno", "srno"),
        example="ADM1023", note="Unique in the school. Uploading the folder again updates the student "
                                "with this admission number."),
    Col("roll_no", "Roll no.", False, 20, aliases=("roll", "rollnumber"), example="12"),
    Col("dob", "Date of birth", False, kind="date", aliases=("dateofbirth", "birthdate"), example="2014-05-21"),
    Col("father_name", "Father's name", False, 100, aliases=("father", "fathername"), example="Suresh Kumar"),
    Col("mother_name", "Mother's name", False, 100, aliases=("mother", "mothername"), example="Anita Kumar"),
    Col("guardian_phone", "Primary phone", True, 20, "phone",
        aliases=("guardianphone", "parentphone", "parentmobile", "guardianmobile", "primarymobile", "mobile"),
        example="9876543210",
        note="The parent's 10-digit mobile number. It is the app username when Username is empty "
             "(brothers and sisters then share it)."),
    Col("username", "Username", False, 50, aliases=("login", "loginname", "user"),
        note="Leave empty: the primary phone is used."),
    Col("email", "Email", False, EMAIL_MAX_LENGTH, "email", aliases=("emailaddress", "mail", "parentemail")),
    Col("phone", "Student's phone", False, 20, "phone", aliases=("studentphone", "studentmobile")),
    Col("address", "Address", False, 255, width=30),
)

COLUMNS = {CLASSES: CLASS_COLUMNS, TEACHERS: TEACHER_COLUMNS, DRIVERS: DRIVER_COLUMNS, STUDENTS: STUDENT_COLUMNS}


# ------------------------------------------------------------------ reading

def header_key(text: str) -> str:
    """'Father’s Name *' / 'father_name' / 'FATHERS NAME' -> 'fathersname'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in decomposed if not unicodedata.combining(c)).lower())


def resolve_headers(kind: str, header: list[str]) -> tuple[dict[str, int], list[str]]:
    """Column key -> index in the row, and the headers nobody knows (ignored)."""
    lookup: dict[str, Col] = {}
    for col in COLUMNS[kind]:
        for spelling in (col.header, col.key, *col.aliases):
            lookup.setdefault(header_key(spelling), col)
    found: dict[str, int] = {}
    unknown: list[str] = []
    for i, text in enumerate(header):
        if not (text or "").strip():
            continue
        col = lookup.get(header_key(text))
        if col is None:
            unknown.append(text.strip())
        elif col.key not in found:
            found[col.key] = i
    return found, unknown


def fold(value: str) -> str:
    """Case- and accent-insensitive key (how MySQL compares names here, utf8mb4_unicode_ci)."""
    decomposed = unicodedata.normalize("NFKD", (value or "").strip())
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().split())


_EXCEL_NUMBER_RE = re.compile(r"^\d(\.\d+)?[eE]\+?\d+$")
_PHONE_CHARS_RE = re.compile(r"^\+?[0-9 ()\-.]+$")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d")


def phone(value: str) -> str:
    """A mobile number as 10 digits ("+91 98765-43210" -> "9876543210"), or ValueError."""
    raw = value.strip()
    if _EXCEL_NUMBER_RE.match(raw):
        raise ValueError("Excel turned this number into 9.87E+09. Format the column as Text and type it again")
    if not _PHONE_CHARS_RE.match(raw):
        raise ValueError("Write the phone number with digits only, e.g. 9876543210")
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("A phone number needs 10 digits, e.g. 9876543210")
    return digits


def parse_date(value: str) -> date:
    raw = value.strip()
    if " " in raw:  # "2014-05-21 00:00:00" from a date cell with a time
        raw = raw.split(" ", 1)[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError("Write the date as YYYY-MM-DD (e.g. 2014-05-21) or DD-MM-YYYY")


def split_list(value: str) -> list[str]:
    """'A, B ,b; C' -> ['A', 'B', 'C'] (commas or semicolons; case-insensitive duplicates dropped)."""
    seen, out = set(), []
    for part in re.split(r"[,;\n]", value or ""):
        name = " ".join(part.split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


# ---------------------------------------------------------------- file names

_SPECIAL = {"classes": CLASSES, "class": CLASSES, "teachers": TEACHERS, "teacher": TEACHERS,
            "drivers": DRIVERS, "driver": DRIVERS}


@dataclass(frozen=True)
class FileName:
    path: str          # as uploaded, folders included ("Green Valley/students/Class 5 - A.xlsx")
    stem: str          # "Class 5 - A"
    kind: str | None   # classes | teachers | drivers | students | None (ignored)
    ignored_reason: str | None = None


def classify(path: str) -> FileName:
    clean = path.replace("\\", "/").strip("/")
    name = clean.rsplit("/", 1)[-1]
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if name.startswith((".", "~$")) or name.lower() in ("thumbs.db", "desktop.ini"):
        return FileName(clean, stem, None, "hidden or temporary file")
    if ext.lower() != "xlsx":
        return FileName(clean, stem, None, "not an Excel (.xlsx) file")
    special = _SPECIAL.get(header_key(stem))
    return FileName(clean, stem.strip(), special or STUDENTS)


def student_file_candidates(stem: str) -> list[tuple[str, str | None]]:
    """Ways to read a student file name as (class, section), most likely first.

    "Class 5 - A" -> [("5", "A"), ("Class 5 - A", None), ...]. The caller keeps the
    first one that names a known class (and section)."""
    text = " ".join(stem.replace("_", " ").split())
    text = re.sub(r"(?i)^class\s+", "", text) or text
    out: list[tuple[str, str | None]] = []
    if "-" in text:
        cls, _, sec = text.rpartition("-")
        if cls.strip() and sec.strip():
            out.append((cls.strip(), sec.strip()))
    out.append((text, None))
    out.append((" ".join(stem.split()), None))  # a class literally named "Class 5"
    parts = text.split(" ")
    if len(parts) > 1:  # "5 A"
        out.append((" ".join(parts[:-1]), parts[-1]))
    return out


def student_file_name(class_name: str, section_name: str | None) -> str:
    label = class_name if not re.fullmatch(r"\d+", class_name or "") else f"Class {class_name}"
    return f"students/{label}{f' - {section_name}' if section_name else ''}.xlsx"


# ---------------------------------------------------------------- templates

_HELP = {
    CLASSES: [
        "classes.xlsx: one row per class",
        "Class name: as the school writes it (5, LKG, Nursery). A number is shown as \"Class 5\".",
        "Sections: e.g. A, B, C. Leave empty if the class is not split into sections.",
        "Subjects: separated by commas, e.g. English, Hindi, Maths.",
        "Uploading again: classes already in the app keep everything they have; new sections and new subjects "
        "from the sheet are added. Nothing is removed. The yearly fee is set later on the Fees page.",
    ],
    TEACHERS: [
        "teachers.xlsx: one row per teacher",
        "Required: Full name, Phone. The phone (10 digits) becomes the username when Username is empty.",
        "Every new teacher gets an easy password (e.g. mango4821). After the upload you download the list of "
        "usernames and passwords; each teacher chooses their own password at the first sign-in.",
        "Class teacher and subject assignments are made in the app afterwards.",
        "Uploading again: a row whose username is already a teacher updates that teacher.",
    ],
    DRIVERS: [
        "drivers.xlsx: one row per driver",
        "Required: Full name, Phone. The phone (10 digits) becomes the username when Username is empty.",
        "Every new driver gets an easy password, listed after the upload. Vehicles are linked in Transport.",
    ],
    STUDENTS: [
        "One file per section, named after it: \"Class 5 - A.xlsx\" (or \"5 - A.xlsx\"). A class without "
        "sections: \"LKG.xlsx\". Put them in a folder called students.",
        "Required: Full name, Admission no., Primary phone.",
        "The primary phone (the parent's mobile) is the app username when Username is empty. Brothers and "
        "sisters with the same number share one username; after the upload you are asked to confirm each of them.",
        "No password is needed: the class teacher gives the parents their login from the app "
        "(New password & share).",
        "Dates as YYYY-MM-DD (2014-05-21) or DD-MM-YYYY. Phone numbers: format the column as Text.",
        "Uploading again: a row whose admission number is already in the app updates that student "
        "(and moves them to this section). Blank cells keep what is saved.",
    ],
}


def _sheet(kind: str, rows: list[list[object]]) -> bytes:
    cols = COLUMNS[kind]
    return spreadsheet.workbook(spreadsheet.Sheet(
        title={CLASSES: "Classes", TEACHERS: "Teachers", DRIVERS: "Drivers", STUDENTS: "Students"}[kind],
        columns=[spreadsheet.Column(
            header=c.header + (" *" if c.required else ""), width=c.width,
            text=c.kind in ("phone", "text", "list") or c.key in ("admission_no", "roll_no"),
            note=(c.note + (f" Example: {c.example}" if c.example else "")).strip() or None,
        ) for c in cols],
        rows=rows,
        help=["How to fill " + {CLASSES: "classes.xlsx", TEACHERS: "teachers.xlsx", DRIVERS: "drivers.xlsx",
                                STUDENTS: "a students file"}[kind], *_HELP[kind][1:]] if kind != CLASSES
        else _HELP[kind],
        blank_rows=200,
    ))


def template_zip(current: dict | None = None, folder: str = "School") -> bytes:
    """The folder as a .zip: classes.xlsx, teachers.xlsx, drivers.xlsx and one students
    file per section. `current` (from service.current_data) fills them with the school's data;
    without it every sheet is empty and there is one example students file."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        data = current or {}
        z.writestr(f"{folder}/classes.xlsx", _sheet(CLASSES, data.get(CLASSES, [])))
        z.writestr(f"{folder}/teachers.xlsx", _sheet(TEACHERS, data.get(TEACHERS, [])))
        z.writestr(f"{folder}/drivers.xlsx", _sheet(DRIVERS, data.get(DRIVERS, [])))
        files = data.get(STUDENTS) or {student_file_name("1", "A"): []}
        for name, rows in files.items():
            z.writestr(f"{folder}/{name}", _sheet(STUDENTS, rows))
        z.writestr(f"{folder}/README.txt", "\r\n".join(
            ["Escolva — bulk school upload", "",
             "Fill the sheets (the 'How to fill' tab of each explains it), then pick this folder in "
             "EduManage HQ → the school → Bulk upload. Nothing is saved until you press Upload, and any "
             "problem in any file stops the whole upload, with the file, row and column to fix.", "",
             *[line for k in (CLASSES, TEACHERS, DRIVERS, STUDENTS) for line in ("", *_HELP[k])]]))
    return out.getvalue()
