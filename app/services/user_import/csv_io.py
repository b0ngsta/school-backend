"""Reading uploaded CSV files and writing CSV downloads.

Reading is forgiving about what spreadsheet programs produce (UTF-8 with or
without BOM, comma/semicolon/tab separators, blank trailing rows, header
spelling) and strict about everything else: a file that is not text, has
missing columns or too many rows is rejected with a message that says how
to fix it.

Downloads are written as UTF-8 with BOM (so Excel shows Hindi/Tamil names
correctly) and with spreadsheet-formula protection: a value starting with
= + - @ is prefixed with an apostrophe, which reading removes again.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from fastapi import UploadFile

from app.core.errors import BadRequest, PayloadTooLarge
from app.services.user_import.columns import Column, header_key, resolve_headers

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_CHUNK = 256 * 1024
REQUIRED_HEADERS = ("username", "full_name", "email")


@dataclass
class CsvRow:
    number: int                    # spreadsheet row number (the header is row 1)
    cells: dict[str, str]          # column name -> trimmed text ("" when empty)
    problems: list[str] = field(default_factory=list)


@dataclass
class ParsedCsv:
    columns: list[Column]
    rows: list[CsvRow]
    warnings: list[str]


def read_upload(upload: UploadFile, max_mb: int) -> bytes:
    limit = max_mb * 1024 * 1024
    buffer = io.BytesIO()
    while chunk := upload.file.read(_CHUNK):
        buffer.write(chunk)
        if buffer.tell() > limit:
            raise PayloadTooLarge(f"The CSV file is larger than {max_mb} MB. Split it into smaller files.")
    return buffer.getvalue()


def parse(data: bytes, user_type: str, max_rows: int) -> ParsedCsv:
    text = _decode(data)
    lines = text.splitlines()
    first_line = next((line for line in lines if line.strip()), "")
    if not first_line:
        raise BadRequest("The CSV file is empty. Download the template and fill in one user per row.")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(first_line), strict=True)

    try:
        records = list(reader)
    except csv.Error as exc:
        raise BadRequest(f"The CSV file could not be read near line {reader.line_num}: {exc}. "
                         "Save it again from your spreadsheet as 'CSV UTF-8'.") from exc

    header_index = next(i for i, rec in enumerate(records) if any(cell.strip() for cell in rec))
    columns, positions, warnings = _map_headers(records[header_index], user_type)

    rows: list[CsvRow] = []
    for offset, record in enumerate(records[header_index + 1:], start=header_index + 2):
        if not any(cell.strip() for cell in record):
            continue  # blank rows (Excel often leaves some at the end)
        cells = {col.name: unescape(record[pos]) if pos < len(record) else "" for col, pos in positions}
        row = CsvRow(number=offset, cells=cells)
        if any(cell.strip() for cell in record[len(records[header_index]):]):
            row.problems.append("This row has more values than there are columns; check for a stray comma.")
        rows.append(row)
        if len(rows) > max_rows:
            raise BadRequest(f"The file has more than {max_rows} users. Split it into smaller files.")
    if not rows:
        raise BadRequest("The CSV file has a header row but no users.")
    return ParsedCsv(columns=columns, rows=rows, warnings=warnings)


def unescape(cell: str) -> str:
    value = cell.strip()
    if len(value) > 1 and value[0] == "'" and value[1] in FORMULA_PREFIXES:
        value = value[1:]  # written by our own export (formula protection)
    return value


def write(headers: list[str], rows: list[list[object]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if v is None else _escape(str(v)) for v in row])
    return ("﻿" + out.getvalue()).encode("utf-8")


def _escape(value: str) -> str:
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def _decode(data: bytes) -> str:
    if data.startswith(b"PK\x03\x04"):
        raise BadRequest("This is an Excel (.xlsx) file, not a CSV. In Excel choose File → Save As → "
                         "'CSV UTF-8 (Comma delimited)'.")
    if b"\x00" in data[:4096]:
        raise BadRequest("This is not a text CSV file. Save it from your spreadsheet as 'CSV UTF-8'.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadRequest("The CSV file is not UTF-8 text. In Excel choose File → Save As → "
                         "'CSV UTF-8 (Comma delimited)'; in Google Sheets use File → Download → CSV.") from exc


def _delimiter(first_line: str) -> str:
    # Excel in many European locales saves with ';'
    counts = {sep: first_line.count(sep) for sep in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _map_headers(header: list[str], user_type: str) -> tuple[list[Column], list[tuple[Column, int]], list[str]]:
    lookup = resolve_headers(user_type)
    positions: list[tuple[Column, int]] = []
    seen: dict[str, str] = {}
    warnings: list[str] = []
    for index, raw in enumerate(header):
        if not raw.strip():
            continue
        column = lookup.get(header_key(raw))
        if column is None:
            warnings.append(f"Column '{raw.strip()}' is not used for {user_type} accounts and was ignored.")
            continue
        if column.name in seen:
            raise BadRequest(f"The columns '{seen[column.name]}' and '{raw.strip()}' are the same field "
                             f"({column.name}); keep only one.")
        seen[column.name] = raw.strip()
        positions.append((column, index))
    missing = [name for name in REQUIRED_HEADERS if name not in seen]
    if missing:
        raise BadRequest(f"Missing column(s): {', '.join(missing)}. Download the template for the exact headers.")
    return [col for col, _ in positions], positions, warnings
