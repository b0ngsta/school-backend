"""Nov 2026 additions for the mobile app (no migration):
* `GET /users?exclude_type=teacher` — the app's Staff users screen (teachers have their own screen)
* `GET /imports/users/template|export …&format=json` — base64 Excel the app saves itself
* attendance stays class-teacher-only (the app now offers only that section)
"""
from __future__ import annotations

import base64
import io

from helpers import bearer
from openpyxl import load_workbook


def test_users_exclude_type(client, school):
    a = bearer(school.admin)
    everyone = client.get("/users", headers=a).json()
    assert any(u["user_type"] == "teacher" for u in everyone)
    r = client.get("/users", params={"exclude_type": "teacher"}, headers=a)
    assert r.status_code == 200
    rows = r.json()
    assert rows and all(u["user_type"] not in ("teacher", "student") for u in rows)
    assert int(r.headers["X-Total-Count"]) == len(rows)


def test_import_template_and_export_as_json(client, school):
    r = client.get("/imports/users/template", params={"user_type": "student", "format": "json"},
                   headers=bearer(school.t1))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"].endswith(".xlsx") and body["mime"].startswith("application/vnd.openxml")
    wb = load_workbook(io.BytesIO(base64.b64decode(body["content_base64"])))
    assert wb.sheetnames == ["Users", "How to fill"]

    ex = client.get("/imports/users/export", params={"user_type": "student", "class_id": school.c1,
                                                     "section_id": school.c1a, "format": "json"},
                    headers=bearer(school.t1))
    assert ex.status_code == 200, ex.text
    assert ex.json()["filename"].endswith(".xlsx")
    # a subject teacher of the section may not export / import it
    no = client.get("/imports/users/export", params={"user_type": "student", "class_id": school.c1,
                                                     "section_id": school.c1a, "format": "json"},
                    headers=bearer(school.t2))
    assert no.status_code == 403
    # the plain Excel download is unchanged
    xlsx = client.get("/imports/users/template", params={"user_type": "student"}, headers=bearer(school.t1))
    assert ".xlsx" in xlsx.headers["content-disposition"]


def test_only_class_teacher_marks_attendance(client, school):
    from app.core.clock import school_today

    body = {"section_id": school.c1a, "date": str(school_today()),
            "records": [{"student_id": school.s1, "status": "present"}]}
    assert client.post("/attendance", json=body, headers=bearer(school.t2)).status_code == 403  # subject teacher
    assert client.post("/attendance", json=body, headers=bearer(school.t1)).status_code in (200, 201)
