"""Report-card designs and print runs saved for later (migration 0025).

The web app's Report Cards screen designs the printed report card (front and
back side) and prints it in the browser, so the server only keeps:

* the school's designs (`print_templates`): name, kind (front / back side),
  the layout JSON and which one is the default of its kind;
* print runs the office put aside (`print_batches`): when some students'
  marks are neither entered nor marked absent, the office can print the
  others and keep those students "waiting to print" until the marks are fixed.

Managers only (routes). Every query is scoped to the caller's school.
"""
from __future__ import annotations

import json

from app.core.errors import BadRequest, NotFound, PayloadTooLarge
from app.db import Db
from app.models.auth import CurrentUser
from app.models.print_templates import PrintBatchCreate, PrintBatchUpdate, PrintTemplateSave
from app.services import audit

MAX_LAYOUT_BYTES = 3 * 1024 * 1024  # pictures inside a design are data: URLs (≤ 700 KB each in the web app)
MAX_TEMPLATES = 200  # per school

_TEMPLATE_SELECT = (
    "SELECT t.id, t.kind, t.name, t.layout, t.is_default, t.created_at, t.updated_at, "
    "cu.full_name AS created_by_name, uu.full_name AS updated_by_name "
    "FROM print_templates t "
    "LEFT JOIN users cu ON cu.id = t.created_by "
    "LEFT JOIN users uu ON uu.id = t.updated_by "
    "WHERE t.org_id = %s"
)

_BATCH_SELECT = (
    "SELECT b.id, b.source, b.exam_id, e.name AS exam_name, b.class_id, c.name AS class_name, "
    "b.front_template_id, b.back_template_id, b.title, b.pending, b.printed_count, b.status, "
    "b.created_at, b.updated_at, u.full_name AS created_by_name "
    "FROM print_batches b "
    "LEFT JOIN exams e ON e.id = b.exam_id "
    "LEFT JOIN classes c ON c.id = b.class_id "
    "LEFT JOIN users u ON u.id = b.created_by "
    "WHERE b.org_id = %s"
)


# ------------------------------------------------------------------ designs

def _template_row(row: dict) -> dict:
    out = dict(row)
    try:
        out["layout"] = json.loads(row["layout"])
    except (TypeError, ValueError):
        out["layout"] = {"elements": []}
    out["is_default"] = bool(row["is_default"])
    return out


def _layout_text(body: PrintTemplateSave) -> str:
    text = json.dumps(body.layout, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise PayloadTooLarge(
            f"This design is too big to save (over {MAX_LAYOUT_BYTES // (1024 * 1024)} MB). "
            "Use smaller pictures in it.")
    return text


def list_templates(db: Db, kind: str | None = None) -> list[dict]:
    sql, params = _TEMPLATE_SELECT, [db.org]
    if kind:
        sql += " AND t.kind = %s"
        params.append(kind)
    rows = db.all(sql + " ORDER BY t.is_default DESC, t.updated_at DESC, t.id DESC", params)
    return [_template_row(r) for r in rows]


def get_template(db: Db, template_id: int) -> dict:
    row = db.one(_TEMPLATE_SELECT + " AND t.id = %s", (db.org, template_id))
    if not row:
        raise NotFound("Design not found")
    return _template_row(row)


def _clear_other_defaults(db: Db, kind: str, keep_id: int) -> None:
    db.execute("UPDATE print_templates SET is_default = 0 WHERE org_id = %s AND kind = %s AND id <> %s "
               "AND is_default = 1", (db.org, kind, keep_id))


def create_template(db: Db, actor: CurrentUser, body: PrintTemplateSave) -> dict:
    layout = _layout_text(body)
    with db.transaction():
        # the school's row is the lock: two saves at once can't both slip past the limit / both be default
        db.one("SELECT id FROM organisations WHERE id = %s FOR UPDATE", (db.org,))
        count = int(db.scalar("SELECT COUNT(*) FROM print_templates WHERE org_id = %s", (db.org,)) or 0)
        if count >= MAX_TEMPLATES:
            raise BadRequest(f"A school can keep at most {MAX_TEMPLATES} designs. Delete some old ones first.")
        template_id = db.execute(
            "INSERT INTO print_templates (org_id, kind, name, layout, is_default, created_by, updated_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (db.org, body.kind, body.name, layout, int(body.is_default), actor.id, actor.id),
        ).lastrowid
        if body.is_default:
            _clear_other_defaults(db, body.kind, template_id)
        audit.record(db, actor, "print_template.create", "print_template", template_id,
                     {"name": body.name, "kind": body.kind})
    return get_template(db, template_id)


def update_template(db: Db, actor: CurrentUser, template_id: int, body: PrintTemplateSave) -> dict:
    layout = _layout_text(body)
    with db.transaction():
        db.one("SELECT id FROM organisations WHERE id = %s FOR UPDATE", (db.org,))
        row = db.one("SELECT id, name, kind FROM print_templates WHERE id = %s AND org_id = %s",
                     (template_id, db.org))
        if not row:
            raise NotFound("Design not found")
        db.execute(
            "UPDATE print_templates SET kind = %s, name = %s, layout = %s, is_default = %s, updated_by = %s, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (body.kind, body.name, layout, int(body.is_default), actor.id, template_id),
        )
        if body.is_default:
            _clear_other_defaults(db, body.kind, template_id)
        audit.record(db, actor, "print_template.update", "print_template", template_id,
                     {"name": body.name, "kind": body.kind, "is_default": body.is_default})
    return get_template(db, template_id)


def delete_template(db: Db, actor: CurrentUser, template_id: int) -> None:
    with db.transaction():
        row = db.one("SELECT name FROM print_templates WHERE id = %s AND org_id = %s FOR UPDATE",
                     (template_id, db.org))
        if not row:
            raise NotFound("Design not found")
        db.execute("DELETE FROM print_templates WHERE id = %s", (template_id,))  # batches keep a NULL link
        audit.record(db, actor, "print_template.delete", "print_template", template_id, {"name": row["name"]})


# ------------------------------------------------------------ waiting to print

def _batch_row(row: dict) -> dict:
    out = dict(row)
    try:
        out["pending"] = json.loads(row["pending"])
    except (TypeError, ValueError):
        out["pending"] = []
    out["pending_count"] = len(out["pending"])
    return out


def list_batches(db: Db, status: str | None = "open") -> list[dict]:
    sql, params = _BATCH_SELECT, [db.org]
    if status:
        sql += " AND b.status = %s"
        params.append(status)
    return [_batch_row(r) for r in db.all(sql + " ORDER BY b.id DESC LIMIT 200", params)]


def get_batch(db: Db, batch_id: int) -> dict:
    row = db.one(_BATCH_SELECT + " AND b.id = %s", (db.org, batch_id))
    if not row:
        raise NotFound("Saved print run not found")
    return _batch_row(row)


def _check_students(db: Db, pending: list) -> str:
    """Every student must be a student of this school; returns the JSON to store."""
    ids = sorted({p.student_id for p in pending})
    if ids:
        marks = ", ".join(["%s"] * len(ids))
        found = int(db.scalar(f"SELECT COUNT(*) FROM users WHERE org_id = %s AND user_type = 'student' "
                              f"AND id IN ({marks})", (db.org, *ids)) or 0)
        if found != len(ids):
            raise NotFound("Student not found")
    sections = sorted({p.section_id for p in pending if p.section_id is not None})
    if sections:
        marks = ", ".join(["%s"] * len(sections))
        found = int(db.scalar(f"SELECT COUNT(*) FROM sections s JOIN classes c ON c.id = s.class_id "
                              f"WHERE c.org_id = %s AND s.id IN ({marks})", (db.org, *sections)) or 0)
        if found != len(sections):
            raise NotFound("Section not found")
    return json.dumps([p.model_dump() for p in pending], ensure_ascii=False)


def _check_template(db: Db, template_id: int | None) -> None:
    if template_id is not None and not db.one("SELECT id FROM print_templates WHERE id = %s AND org_id = %s",
                                              (template_id, db.org)):
        raise NotFound("Design not found")


def create_batch(db: Db, actor: CurrentUser, body: PrintBatchCreate) -> dict:
    if body.source == "exam" and body.exam_id is None:
        raise BadRequest("exam_id is required for an exam's result")
    with db.transaction():
        if body.exam_id is not None and not db.one("SELECT id FROM exams WHERE id = %s AND org_id = %s",
                                                   (body.exam_id, db.org)):
            raise NotFound("Exam not found")
        if body.class_id is not None and not db.one("SELECT id FROM classes WHERE id = %s AND org_id = %s",
                                                    (body.class_id, db.org)):
            raise NotFound("Class not found")
        _check_template(db, body.front_template_id)
        _check_template(db, body.back_template_id)
        pending = _check_students(db, body.pending)
        batch_id = db.execute(
            "INSERT INTO print_batches (org_id, source, exam_id, class_id, front_template_id, back_template_id, "
            "title, pending, printed_count, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (db.org, body.source, body.exam_id, body.class_id, body.front_template_id, body.back_template_id,
             body.title.strip(), pending, body.printed_count, actor.id),
        ).lastrowid
        audit.record(db, actor, "print_batch.create", "print_batch", batch_id,
                     {"title": body.title.strip(), "waiting": len(body.pending)})
    return get_batch(db, batch_id)


def update_batch(db: Db, actor: CurrentUser, batch_id: int, body: PrintBatchUpdate) -> dict:
    with db.transaction():
        row = db.one("SELECT id, status FROM print_batches WHERE id = %s AND org_id = %s FOR UPDATE",
                     (batch_id, db.org))
        if not row:
            raise NotFound("Saved print run not found")
        sets, params = [], []
        if body.pending is not None:
            sets.append("pending = %s")
            params.append(_check_students(db, body.pending))
            if not body.pending and body.status is None:
                sets.append("status = 'done'")  # nobody left waiting
        if body.printed_count is not None:
            sets.append("printed_count = %s")
            params.append(body.printed_count)
        if body.status is not None:
            sets.append("status = %s")
            params.append(body.status)
        if sets:
            db.execute(f"UPDATE print_batches SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP "
                       "WHERE id = %s", (*params, batch_id))
            audit.record(db, actor, "print_batch.update", "print_batch", batch_id,
                         {"waiting": None if body.pending is None else len(body.pending), "status": body.status})
    return get_batch(db, batch_id)


def delete_batch(db: Db, actor: CurrentUser, batch_id: int) -> None:
    with db.transaction():
        row = db.one("SELECT title FROM print_batches WHERE id = %s AND org_id = %s FOR UPDATE", (batch_id, db.org))
        if not row:
            raise NotFound("Saved print run not found")
        db.execute("DELETE FROM print_batches WHERE id = %s", (batch_id,))
        audit.record(db, actor, "print_batch.delete", "print_batch", batch_id, {"title": row["title"]})
