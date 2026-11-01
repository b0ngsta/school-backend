"""Notices for chosen classes and teachers' class announcements (migration 0018).

School (helpers.School): Class 1 — A (student1, student2; teacher1 class teacher,
teacher2 Math), Class 1 — B (student3), Class 2 — A (student4; teacher3).
"""
from __future__ import annotations

import pytest
from helpers import PDF_BYTES, bearer, login, png_bytes


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def _titles(client, who, path):
    return [n["title"] for n in _ok(client.get(path, headers=bearer(who)))]


def _notified(client, who, kind):
    return [n["title"] for n in _ok(client.get("/notifications", headers=bearer(who))) if n["type"] == kind]


@pytest.fixture
def kids(client, school):
    return {n: login(client, f"student{n}", "studpass") for n in (1, 2, 3, 4)}


# -------------------------------------------------------------- notice board

def test_notice_for_some_classes_reaches_only_them(client, school, kids):
    a = bearer(school.admin)
    whole = _ok(client.post("/notices", headers=a, json={"title": "Holiday", "body": "Closed on Friday"}), 201)
    assert whole["audience"] == "school" and whole["class_ids"] == []
    only1 = _ok(client.post("/notices", headers=a, json={"title": "PTM Class 1", "body": "Saturday",
                                                         "class_ids": [school.c1]}), 201)
    assert only1["audience"] == "classes" and only1["audience_label"] == "Class 1"
    both = _ok(client.post("/notices", headers=a, json={"title": "Trip", "body": "Zoo",
                                                        "class_ids": [school.c2, school.c1, school.c1]}), 201)
    assert both["class_ids"] == sorted([school.c1, school.c2]) and both["audience_label"] == "Class 1, Class 2"
    # [] = whole school too
    empty = _ok(client.post("/notices", headers=a, json={"title": "All", "body": "x", "class_ids": []}), 201)
    assert empty["audience"] == "school"

    # parents: the list, the detail and the home only show their own notices
    assert set(_titles(client, kids[1], "/student/notices")) == {"Holiday", "PTM Class 1", "Trip", "All"}
    assert set(_titles(client, kids[3], "/student/notices")) == {"Holiday", "PTM Class 1", "Trip", "All"}
    assert set(_titles(client, kids[4], "/student/notices")) == {"Holiday", "Trip", "All"}
    _ok(client.get(f"/student/notices/{only1['id']}", headers=bearer(kids[4])), 404)
    assert _ok(client.get(f"/student/notices/{only1['id']}", headers=bearer(kids[1])))["audience_label"] == "Class 1"
    home4 = _ok(client.get("/student/dashboard", headers=bearer(kids[4])))
    assert "PTM Class 1" not in [n["title"] for n in home4["latest_notices"]]

    # notifications follow the same rule
    assert "📢 PTM Class 1" in _notified(client, kids[3], "notice")
    assert "📢 PTM Class 1" not in _notified(client, kids[4], "notice")
    assert "📢 PTM Class 1" in _notified(client, school.t2, "notice")  # teaches Class 1
    assert "📢 PTM Class 1" not in _notified(client, school.t3, "notice")
    assert "📢 PTM Class 1" in _notified(client, login(client, "principal"), "notice")

    # staff board: managers everything, teachers school-wide + their classes
    assert len(_titles(client, school.admin, "/notices")) == 4
    assert "PTM Class 1" not in _titles(client, school.t3, "/notices")
    assert "PTM Class 1" in _titles(client, school.t1, "/notices")
    listed = {n["title"]: n for n in _ok(client.get("/notices", headers=a))}
    assert listed["Trip"]["class_ids"] == sorted([school.c1, school.c2])

    # red dots: Class 2 never hears about the Class 1 notice
    assert _ok(client.get("/updates", headers=bearer(kids[4])))["sections"]["notices"]["latest_at"] is not None


def test_notice_classes_are_checked(client, school):
    a = bearer(school.admin)
    _ok(client.post("/notices", headers=a, json={"title": "x", "body": "y", "class_ids": [999999]}), 404)
    _ok(client.post("/notices", headers=bearer(school.t1), json={"title": "x", "body": "y",
                                                                 "class_ids": [school.c1]}), 403)


def test_deleting_the_only_class_never_makes_a_notice_school_wide(client, school, kids):
    a = bearer(school.admin)
    c3 = _ok(client.post("/classes", headers=a, json={"name": "Class 3"}), 201)["id"]
    _ok(client.post("/notices", headers=a, json={"title": "Only 3", "body": "x", "class_ids": [c3]}), 201)
    _ok(client.delete(f"/classes/{c3}", headers=a))
    assert "Only 3" not in _titles(client, kids[1], "/student/notices")
    assert "Only 3" in _titles(client, school.admin, "/notices")


# --------------------------------------------------------- class announcements

def test_teacher_announces_to_the_classes_they_teach(client, school, kids):
    t2 = bearer(school.t2)
    targets = _ok(client.get("/class-announcements/targets", headers=t2))
    assert [(t["section_id"], t["class_teacher"], t["subjects"]) for t in targets] == [(school.c1a, False, "Math")]
    assert len(_ok(client.get("/class-announcements/targets", headers=bearer(school.admin)))) == 3

    made = _ok(client.post("/class-announcements", headers=t2, json={
        "title": " Maths test ", "body": "Chapter 4 on Monday", "section_ids": [school.c1a]}), 201)
    assert made["title"] == "Maths test" and made["recipients_count"] == 2
    assert made["audience_label"] == "Class 1 — A" and made["section_ids"] == [school.c1a]

    # a file (PDF or photo), replaced, removed
    att = _ok(client.post(f"/class-announcements/{made['id']}/attachment", headers=t2,
                          files={"file": ("ch4.pdf", PDF_BYTES, "application/pdf")}))
    assert att["attachment_type"] == "pdf" and att["attachment_name"] == "ch4.pdf"
    att = _ok(client.post(f"/class-announcements/{made['id']}/attachment", headers=t2,
                          files={"file": ("board.png", png_bytes(), "image/png")}))
    assert att["attachment_type"] == "image"
    _ok(client.post(f"/class-announcements/{made['id']}/attachment", headers=t2,
                    files={"file": ("x.txt", b"hello", "text/plain")}), 415)

    # parents of that section see it and got a push; others don't
    mine = _ok(client.get("/student/class-announcements", headers=bearer(kids[1])))
    assert [m["title"] for m in mine] == ["Maths test"] and mine[0]["posted_by"] == "Teacher 2"
    assert mine[0]["attachment_path"]  # signed URL
    assert _ok(client.get(f"/student/class-announcements/{made['id']}", headers=bearer(kids[2])))["body"]
    assert _ok(client.get("/student/class-announcements", headers=bearer(kids[3]))) == []
    _ok(client.get(f"/student/class-announcements/{made['id']}", headers=bearer(kids[4])), 404)
    pushes = [n for n in _ok(client.get("/notifications", headers=bearer(kids[1])))
              if n["type"] == "class_announcement"]
    assert pushes and pushes[0]["data"]["route"] == "ClassAnnouncements"
    assert pushes[0]["data"]["params"] == {"id": made["id"]}
    assert not _notified(client, kids[4], "class_announcement")
    dots = _ok(client.get("/updates", headers=bearer(kids[1])))["sections"]
    assert dots["class_announcements"]["latest_at"] is not None
    assert _ok(client.get("/updates", headers=bearer(kids[4])))["sections"]["class_announcements"]["latest_at"] is None

    # only classes they teach
    _ok(client.post("/class-announcements", headers=t2, json={
        "title": "x", "body": "y", "section_ids": [school.c1a, school.c2a]}), 403)
    _ok(client.post("/class-announcements", headers=t2, json={"title": "x", "body": "y", "section_ids": [999999]}),
        404)
    _ok(client.post("/class-announcements", headers=t2, json={"title": "x", "body": "y", "section_ids": []}), 422)
    _ok(client.post("/class-announcements", headers=t2, json={"title": "  ", "body": "y",
                                                              "section_ids": [school.c1a]}), 400)
    _ok(client.post("/class-announcements", headers=bearer(kids[1]), json={
        "title": "x", "body": "y", "section_ids": [school.c1a]}), 403)
    _ok(client.post("/class-announcements", headers=bearer(login(client, "driver")), json={
        "title": "x", "body": "y", "section_ids": [school.c1a]}), 403)

    # edit / delete: the author or managers; other teachers don't even see it
    t1 = bearer(school.t1)
    _ok(client.put(f"/class-announcements/{made['id']}", headers=t1, json={"title": "x", "body": "y"}), 404)
    _ok(client.delete(f"/class-announcements/{made['id']}", headers=t1), 404)
    assert _ok(client.get("/class-announcements", headers=t1)) == []
    edited = _ok(client.put(f"/class-announcements/{made['id']}", headers=t2,
                            json={"title": "Maths test moved", "body": "Tuesday"}))
    assert edited["title"] == "Maths test moved"
    a = bearer(school.admin)
    assert [x["id"] for x in _ok(client.get(f"/class-announcements?section_id={school.c1a}", headers=a))] \
        == [made["id"]]
    assert _ok(client.get(f"/class-announcements?section_id={school.c2a}", headers=a)) == []
    _ok(client.delete(f"/class-announcements/{made['id']}/attachment", headers=t2), 204)
    assert _ok(client.get(f"/class-announcements/{made['id']}", headers=t2))["attachment_path"] is None
    _ok(client.delete(f"/class-announcements/{made['id']}", headers=a), 204)
    assert _ok(client.get("/student/class-announcements", headers=bearer(kids[1]))) == []


def test_announcement_to_several_sections(client, school, kids):
    a = bearer(school.admin)
    made = _ok(client.post("/class-announcements", headers=a, json={
        "title": "Sports day", "body": "Wear white", "section_ids": [school.c2a, school.c1b, school.c1a]}), 201)
    assert made["recipients_count"] == 4
    assert made["audience_label"] == "Class 1 — A, B · Class 2 — A"
    for n in (1, 3, 4):
        assert _titles(client, kids[n], "/student/class-announcements") == ["Sports day"]
    # paging
    r = client.get("/student/class-announcements?limit=1", headers=bearer(kids[1]))
    assert r.headers["X-Total-Count"] == "1"
