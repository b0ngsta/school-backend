"""Report-card designs + report cards waiting to print (migration 0025)."""
from __future__ import annotations

from datetime import timedelta

from helpers import bearer, login

from app.core.clock import school_today
from app.services import print_templates


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def _layout(text="{{student.name}}"):
    return {"v": 1, "page": {"size": "A4", "orientation": "portrait"},
            "elements": [{"id": "e1", "type": "text", "x": 10, "y": 10, "w": 80, "h": 10, "text": text}]}


def test_office_saves_designs_one_default_per_kind(client, school):
    a, t = bearer(school.admin), bearer(school.t1)
    s = bearer(login(client, "student1", "studpass"))
    assert _ok(client.get("/print-templates", headers=a)) == []

    def save(body, code=201, headers=a, template_id=None):
        if template_id:
            return _ok(client.put(f"/print-templates/{template_id}", headers=headers, json=body), code)
        return _ok(client.post("/print-templates", headers=headers, json=body), code)

    classic = save({"name": "  Classic  ", "kind": "report_card", "is_default": True, "layout": _layout()})
    assert classic["name"] == "Classic" and classic["is_default"] and classic["kind"] == "report_card"
    assert classic["layout"]["elements"][0]["text"] == "{{student.name}}"
    assert classic["created_by_name"] and classic["updated_by_name"]

    # a second default of the same kind takes over; a back side has its own default
    modern = save({"name": "Modern", "is_default": True, "layout": _layout("x")})
    back = save({"name": "Grading key", "kind": "report_card_back", "is_default": True, "layout": _layout("y")})
    fronts = _ok(client.get("/print-templates?kind=report_card", headers=a))
    assert [(x["name"], x["is_default"]) for x in fronts] == [("Modern", True), ("Classic", False)]
    assert [x["id"] for x in _ok(client.get("/print-templates?kind=report_card_back", headers=a))] == [back["id"]]
    assert len(_ok(client.get("/print-templates", headers=a))) == 3
    _ok(client.get("/print-templates?kind=poster", headers=a), 422)

    # saving Classic as default again moves the default back
    edited = save({"name": "Classic 2", "is_default": True, "layout": _layout("z")}, 200, template_id=classic["id"])
    assert edited["name"] == "Classic 2" and edited["layout"]["elements"][0]["text"] == "z"
    assert not _ok(client.get(f"/print-templates/{modern['id']}", headers=a))["is_default"]
    assert _ok(client.get(f"/print-templates/{back['id']}", headers=a))["is_default"]  # other kind untouched

    # managers only
    for h in (t, s):
        _ok(client.get("/print-templates", headers=h), 403)
        save({"name": "x", "layout": _layout()}, 403, headers=h)
    coordinator = bearer(login(client, "coordinator"))
    assert len(_ok(client.get("/print-templates", headers=coordinator))) == 3

    # shape and size checks
    save({"name": "   ", "layout": _layout()}, 422)
    save({"name": "x", "layout": {"elements": "nope"}}, 422)
    save({"name": "x", "layout": {"elements": [1, 2]}}, 422)
    save({"name": "x", "layout": {"elements": [{}] * 401}}, 422)
    save({"name": "x", "kind": "poster", "layout": _layout()}, 422)
    huge = "data:image/png;base64," + "A" * (print_templates.MAX_LAYOUT_BYTES + 10)
    save({"name": "x", "layout": {"elements": [{"type": "image", "src": huge}]}}, 413)

    _ok(client.delete(f"/print-templates/{modern['id']}", headers=t), 403)
    _ok(client.delete(f"/print-templates/{modern['id']}", headers=a), 204)
    _ok(client.delete(f"/print-templates/{modern['id']}", headers=a), 404)
    _ok(client.get(f"/print-templates/{modern['id']}", headers=a), 404)
    save({"name": "x", "layout": _layout()}, 404, template_id=modern["id"])


def test_report_cards_waiting_to_print(client, school):
    a, t = bearer(school.admin), bearer(school.t1)
    exam = _ok(client.post("/exams", headers=a, json={
        "name": "Half Yearly", "class_id": school.c1, "start_date": str(school_today()),
        "end_date": str(school_today() + timedelta(days=5))}), 201)["id"]
    front = _ok(client.post("/print-templates", headers=a, json={"name": "Front", "layout": _layout()}), 201)
    back = _ok(client.post("/print-templates", headers=a, json={
        "name": "Back", "kind": "report_card_back", "layout": _layout()}), 201)
    assert _ok(client.get("/print-batches", headers=a)) == []

    body = {"source": "exam", "exam_id": exam, "class_id": school.c1, "front_template_id": front["id"],
            "back_template_id": back["id"], "title": "Half Yearly · Class 1", "printed_count": 2,
            "pending": [{"student_id": school.s2, "section_id": school.c1a, "full_name": "Student 2",
                         "missing": ["Maths", "Science"]},
                        {"student_id": school.s3, "section_id": school.c1b, "missing": ["Maths"]}]}
    batch = _ok(client.post("/print-batches", headers=a, json=body), 201)
    assert (batch["exam_name"], batch["class_name"], batch["status"], batch["pending_count"]) == \
        ("Half Yearly", "Class 1", "open", 2)
    assert batch["pending"][0]["missing"] == ["Maths", "Science"] and batch["created_by_name"]
    assert [b["id"] for b in _ok(client.get("/print-batches", headers=a))] == [batch["id"]]

    # bad input
    _ok(client.post("/print-batches", headers=a, json={**body, "exam_id": None}), 400)
    _ok(client.post("/print-batches", headers=a, json={**body, "pending": []}), 422)
    _ok(client.post("/print-batches", headers=a, json={**body, "exam_id": 999999}), 404)
    _ok(client.post("/print-batches", headers=a, json={**body, "front_template_id": 999999}), 404)
    _ok(client.post("/print-batches", headers=a, json={
        **body, "pending": [{"student_id": school.t1_id, "missing": []}]}), 404)  # a teacher is no student
    _ok(client.post("/print-batches", headers=t, json=body), 403)
    _ok(client.get("/print-batches", headers=t), 403)

    # one student printed later: the other keeps waiting
    left = _ok(client.put(f"/print-batches/{batch['id']}", headers=a, json={
        "pending": [body["pending"][1]], "printed_count": 3}))
    assert (left["pending_count"], left["printed_count"], left["status"]) == (1, 3, "open")

    # deleting a design keeps the run (its link becomes empty)
    _ok(client.delete(f"/print-templates/{back['id']}", headers=a), 204)
    assert _ok(client.get(f"/print-batches/{batch['id']}", headers=a))["back_template_id"] is None

    # nobody left waiting → done, gone from the open list, still in "all"
    done = _ok(client.put(f"/print-batches/{batch['id']}", headers=a, json={"pending": []}))
    assert (done["status"], done["pending_count"]) == ("done", 0)
    assert _ok(client.get("/print-batches", headers=a)) == []
    assert [b["id"] for b in _ok(client.get("/print-batches?status=all", headers=a))] == [batch["id"]]
    reopened = _ok(client.put(f"/print-batches/{batch['id']}", headers=a, json={"status": "open"}))
    assert reopened["status"] == "open"

    _ok(client.delete(f"/print-batches/{batch['id']}", headers=a), 204)
    _ok(client.get(f"/print-batches/{batch['id']}", headers=a), 404)
    _ok(client.put(f"/print-batches/{batch['id']}", headers=a, json={"status": "done"}), 404)
