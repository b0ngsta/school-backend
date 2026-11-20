"""20 Nov 2026 (no migration), from the backend test report:

* nothing new is added for a student who left the school: no fee, no payment, no remark
  (409 `student_left`, "X has left the school"); their old fees stay readable and payable;
  generating class fees says how many students who left this session were not billed;
* a double click / double tap no longer saves twice: two "Add student" with the same username
  at the same moment, two fee claim forms for one fee at the same moment;
* a value the database can't store (huge amount, very long text) is a 422 with a message, not a 500.
"""
from __future__ import annotations

import threading

from fastapi.testclient import TestClient
from helpers import PDF_BYTES, bearer, login

from app.core.clock import school_today
from app.main import app


def _at_once(request, n: int = 2) -> list[int]:
    """Send `request(client)` from n threads released at the same instant; their status codes."""
    codes: list[int] = [0] * n
    gate = threading.Barrier(n)

    def run(i: int) -> None:
        with TestClient(app) as c:
            gate.wait()
            codes[i] = request(c).status_code

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(codes)


# ------------------------------------------------------------------ students who left

def test_nothing_new_is_added_for_a_student_who_left(client, school):
    a = bearer(school.admin)
    old_fee = client.post("/fees", headers=a, json={"student_id": school.s2, "title": "Term 1", "amount": 500})
    assert old_fee.status_code == 201
    assert client.delete(f"/students/{school.s2}?reason=moved", headers=a).status_code == 200

    for r, words in (
        (client.post("/fees", headers=a, json={"student_id": school.s2, "title": "Late", "amount": 100}),
         "add a fee"),
        (client.post("/transactions", headers=a, json={"student_id": school.s2, "amount": 10, "method": "cash"}),
         "record a payment"),
        (client.post(f"/students/{school.s2}/remarks", headers=a, json={"remark": "x"}), "add a remark"),
    ):
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "student_left"
        assert r.json()["detail"] == f"Student 2 has left the school. Reactivate them first to {words}."

    # what was there before stays: the old fee is listed and can still be paid (dues settled on leaving)
    fees = client.get("/fees", params={"student_id": school.s2}, headers=a).json()
    assert [f["title"] for f in fees] == ["Term 1"]
    assert client.put(f"/fees/{old_fee.json()['id']}/pay", headers=a, json={"method": "cash"}).status_code == 200
    # a payment without a student, and one for a current student, work as before
    assert client.post("/transactions", headers=a, json={"amount": 10, "method": "cash"}).status_code == 201
    assert client.post("/transactions", headers=a, json={"student_id": school.s1, "amount": 10,
                                                          "method": "cash"}).status_code == 201
    # unknown student: still 404
    assert client.post("/fees", headers=a, json={"student_id": 999999, "title": "x", "amount": 1}).status_code == 404

    # back in school -> fees again
    assert client.post(f"/students/{school.s2}/reactivate", headers=a).status_code == 200
    assert client.post("/fees", headers=a, json={"student_id": school.s2, "title": "Late", "amount": 100}
                       ).status_code == 201


def test_generate_says_how_many_who_left_were_not_billed(client, school):
    a = bearer(school.admin)
    assert client.delete(f"/students/{school.s3}?reason=moved", headers=a).status_code == 200  # Class 1 — B
    r = client.post("/fees/generate", headers=a, json={"class_id": school.c1, "title": "Term 2", "amount": 100})
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["created"], body["students"], body["skipped_left"]) == (2, 2, 1)
    titles = {f["student_id"] for f in client.get("/fees", headers=a).json() if f["title"] == "Term 2"}
    assert titles == {school.s1, school.s2}
    # a class nobody left: 0
    r = client.post("/fees/generate", headers=a, json={"class_id": school.c2, "title": "Term 2", "amount": 100})
    assert r.json()["skipped_left"] == 0


# ------------------------------------------------------------------ double submits

def test_two_add_student_at_once_save_one(client, school):
    a = bearer(school.admin)
    codes = _at_once(lambda c: c.post("/students", headers=a, json={
        "username": "twin", "password": "studpass", "full_name": "Twin", "class_id": school.c1,
        "section_id": school.c1a}))
    assert codes == [201, 409]
    rows = [s for s in client.get("/students", params={"q": "twin"}, headers=a).json() if s["username"] == "twin"]
    assert len(rows) == 1
    # confirming brother / sister still shares the username, as before
    r = client.post("/students", headers=a, json={"username": "twin", "password": "studpass2", "full_name": "Twin B",
                                                  "class_id": school.c1, "section_id": school.c1a, "sibling": True})
    assert r.status_code == 201, r.text


def test_two_fee_claim_forms_for_one_fee_at_once_save_one(client, school):
    a = bearer(school.admin)
    fee = client.post("/fees", headers=a, json={"student_id": school.s1, "title": "Term 3", "amount": 70}).json()["id"]
    parent = bearer(login(client, "student1", "studpass"))

    def send(c):
        return c.post("/student/fee-claims", headers=parent, data={
            "amount": "70", "paid_on": str(school_today()), "method": "upi", "fee_id": str(fee)},
            files={"file": ("proof.pdf", PDF_BYTES, "application/pdf")})

    assert _at_once(send) == [201, 409]
    forms = [f for f in client.get("/fee-claim-forms", headers=a).json() if f["fee_id"] == fee]
    assert len(forms) == 1


# ------------------------------------------------------------------ values too large

def test_values_too_large_are_422_not_500(client, school):
    a = bearer(school.admin)
    for path, body in (
        ("/fees", {"student_id": school.s1, "title": "X", "amount": 1e15}),
        ("/transactions", {"student_id": school.s1, "amount": 1e15, "method": "cash"}),
        ("/salaries", {"user_id": school.t1_id, "month": 1, "year": 2026, "amount": 1e15}),
        ("/classes", {"name": "Huge", "fee_amount": 1e15}),
    ):
        r = client.post(path, headers=a, json=body)
        assert r.status_code == 422, (path, r.text)
    # the most a field allows still works
    assert client.post("/fees", headers=a, json={"student_id": school.s1, "title": "Big", "amount": 10_000_000}
                       ).status_code == 201

    # text longer than its column: the database refuses it -> 422 with a message, nothing saved
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/notices", headers=a, json={"title": "Big", "body": "x" * 70_000})
    assert r.status_code == 422, r.text
    assert r.json() == {"detail": "A text is too long. Please check what you entered.", "code": "value_out_of_range"}
    assert not [n for n in client.get("/notices", headers=a).json() if n["title"] == "Big"]
    # a normal notice is unchanged
    assert client.post("/notices", headers=a, json={"title": "Small", "body": "x" * 5_000}).status_code == 201
