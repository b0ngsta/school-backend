"""Excel (.xlsx) files for uploads and templates (Nov 2026), shared by the
user import and the exam-results upload.

* `read_rows(data)` turns an uploaded .xlsx — or, for older clients, a CSV —
  into rows of trimmed text cells. Numbers come back as plain text
  ("72", "72.5", "9876543210"), dates as YYYY-MM-DD.
* `workbook(...)` writes a friendly template: bold coloured header row, frozen
  header, sensible column widths, every cell stored as TEXT where the column
  says so (keeps phone numbers / leading zeros), optional per-column data
  validation and a "How to fill" sheet. Values starting with = + - @ are
  written as text, never as formulas.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field

from app.core.errors import BadRequest

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ZIP = b"PK\x03\x04"
_OLE = b"\xd0\xcf\x11\xe0"  # old .xls


def is_xlsx(data: bytes) -> bool:
    return data.startswith(_ZIP)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.10g}"
    return str(value).strip()


def read_rows(data: bytes, *, what: str = "file") -> list[list[str]]:
    """Rows of the first worksheet (.xlsx) or of a CSV, as text."""
    if not data or not data.strip():
        raise BadRequest(f"The {what} is empty.")
    if data.startswith(_OLE):
        raise BadRequest("This is an old Excel 97-2003 (.xls) file. In Excel choose File → Save As → "
                         "'Excel Workbook (.xlsx)' and upload that.")
    if is_xlsx(data):
        return _read_xlsx(data, what)
    return _read_csv(data, what)


def _read_xlsx(data: bytes, what: str) -> list[list[str]]:
    from openpyxl import load_workbook  # imported lazily: only uploads need it

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — any parse failure means "not a usable workbook"
        raise BadRequest(f"The {what} could not be opened as an Excel workbook. Save it again in Excel "
                         "(File → Save As → Excel Workbook .xlsx) and upload it.") from exc
    try:
        sheet = next((ws for ws in wb.worksheets if getattr(ws, "sheet_state", "visible") == "visible"),
                     wb.worksheets[0])
        rows = [[_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
    finally:
        wb.close()
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        raise BadRequest(f"The {what} has no rows.")
    return rows


def _read_csv(data: bytes, what: str) -> list[list[str]]:
    if b"\x00" in data[:4096]:
        raise BadRequest(f"The {what} is not an Excel (.xlsx) or CSV file.")
    text = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise BadRequest(f"The {what} could not be read. Upload the Excel (.xlsx) template.")
    first = next((line for line in text.splitlines() if line.strip()), "")
    counts = {sep: first.count(sep) for sep in (",", ";", "\t")}
    delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    try:
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    except csv.Error as exc:
        raise BadRequest(f"The {what} could not be read: {exc}.") from exc


# ---------------------------------------------------------------- writing

@dataclass
class Column:
    header: str
    width: float = 14
    text: bool = False            # store as text (phone numbers, ids with leading zeros)
    locked_look: bool = False     # grey "don't edit" fill (student id / name columns)
    validation: str | None = None  # Excel formula for this column's cells, {cell} = the cell (e.g. E2)
    validation_error: str | None = None
    note: str | None = None       # comment on the header cell


@dataclass
class Sheet:
    title: str
    columns: list[Column]
    rows: list[list[object]] = field(default_factory=list)
    help: list[str] = field(default_factory=list)  # lines for a second "How to fill" sheet
    blank_rows: int = 0  # extra empty rows that still get the validation


def workbook(sheet: Sheet) -> bytes:
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = sheet.title[:31]
    head_fill = PatternFill("solid", fgColor="4F46E5")
    grey = PatternFill("solid", fgColor="F1F2F6")
    for i, col in enumerate(sheet.columns, start=1):
        cell = ws.cell(row=1, column=i, value=col.header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if col.note:
            cell.comment = Comment(col.note, "EduManage")
        ws.column_dimensions[get_column_letter(i)].width = col.width
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    for r, values in enumerate(sheet.rows, start=2):
        for i, (col, value) in enumerate(zip(sheet.columns, values, strict=False), start=1):
            cell = ws.cell(row=r, column=i)
            if value is None or value == "":
                cell.value = None
            elif isinstance(value, str):
                cell.value = value
                cell.data_type = "s"  # never a formula, even when it starts with "="
            else:
                cell.value = value
            if col.text:
                cell.number_format = "@"
            if col.locked_look:
                cell.fill = grey

    last = max(1 + len(sheet.rows) + sheet.blank_rows, 2)
    for i, col in enumerate(sheet.columns, start=1):
        letter = get_column_letter(i)
        if col.text:
            for r in range(len(sheet.rows) + 2, last + 1):
                ws.cell(row=r, column=i).number_format = "@"
        if col.validation:
            dv = DataValidation(type="custom", formula1=col.validation.format(cell=f"{letter}2"), allow_blank=True,
                                showErrorMessage=True, errorTitle="Check this value",
                                error=col.validation_error or "This value is not allowed here.")
            dv.add(f"{letter}2:{letter}{last}")
            ws.add_data_validation(dv)

    if sheet.help:
        hs = wb.create_sheet("How to fill")
        hs.column_dimensions["A"].width = 110
        for r, line in enumerate(sheet.help, start=1):
            c = hs.cell(row=r, column=1, value=line)
            c.data_type = "s"
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if r == 1:
                c.font = Font(bold=True, size=13, color="4F46E5")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
