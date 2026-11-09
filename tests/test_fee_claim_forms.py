"""Fee claim forms (migration 0020): a parent sends a payment claim with one
file; the class teacher of the child's section or a manager accepts / declines.
The older per-fee payment proofs (/fees/{id}/claims) keep working unchanged.

School (helpers.School): Class 1 — A (student1, student2; teacher1 class teacher,
teacher2 Math), Class 1 — B (student3, no class teacher), Class 2 — A (student4; teacher3).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from helpers import PDF_BYTES, bearer, login, png_bytes

from app.core.clock import school_today


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


@pytest.fixture
def kids(client, school):
    return {n: login(client, f"student{n}", "studpass") for n in (1, 2, 3, 4)}


def _send(client, who, *, file=("receipt.pdf", PDF_BYTES, "application/pdf"), **fields):
    data = {"amount": "2500", "paid_on": str(school_today()), "method": "upi", **fields}
    data = {k: str(v) for k, v in data.items() if v is not None}
    return client.post("/student/fee-claims", headers=bearer(who), data=data, files={"file": file})


def _ids(client, who, path="/fee-claim-forms"):
    return [f["id"] for f in _ok(client.get(path, headers=bearer(who)))]


def _notes(client, who):
    return [n for n in _ok(client.get("/notifications", headers=bearer(who))) if n["type"] == "fee_claim_form"]


def test_class_teacher_and_managers_review_a_parents_form(client, school, kids):
    form = _ok(_send(client, kids[1], purpose="Term 2 tuition", reference_no="UTR123", note="Paid at bank"), 201)
    assert form["status"] == "pending" and form["purpose"] == "Term 2 tuition"
    assert form["attachment_type"] == "pdf" and form["attachment_path"].startswith("uploads/")
    assert (form["class_name"], form["section_name"], form["fee_id"]) == ("Class 1", "A", None)
    assert float(form["amount"]) == 2500 and form["reference_no"] == "UTR123"

    # the parent sees only their own forms
    assert _ids(client, kids[1], "/student/fee-claims") == [form["id"]]
    assert _ids(client, kids[2], "/student/fee-claims") == []
    _ok(client.get(f"/student/fee-claims/{form['id']}", headers=bearer(kids[2])), 404)

    # reviewers: class teacher of 1-A and managers; not the subject teacher, not another class teacher
    assert form["id"] in _ids(client, school.t1)
    assert form["id"] in _ids(client, school.admin)
    assert form["id"] in _ids(client, login(client, "principal"))
    assert _ids(client, school.t2) == [] and _ids(client, school.t3) == []
    assert _ok(client.get("/fee-claim-forms/stats", headers=bearer(school.t2)))["can_review"] is False
    t1_stats = _ok(client.get("/fee-claim-forms/stats", headers=bearer(school.t1)))
    assert t1_stats["can_review"] and t1_stats["pending"] == 1 and t1_stats["sections"][0]["label"] == "Class 1 — A"
    _ok(client.get(f"/fee-claim-forms/{form['id']}", headers=bearer(school.t3)), 404)
    _ok(client.put(f"/fee-claim-forms/{form['id']}/accept", headers=bearer(school.t2), json={}), 404)
    _ok(client.get("/fee-claim-forms", headers=bearer(kids[1])), 403)  # parents don't review

    # reviewers hear about it (push + inbox) and get a red dot
    assert _notes(client, school.t1) and _notes(client, school.admin)
    assert not _notes(client, school.t2) and not _notes(client, school.t3)
    dots = _ok(client.get("/updates", headers=bearer(school.t1)))["sections"]
    assert dots["fee_claim_forms"]["latest_at"] is not None
    assert _ok(client.get("/updates", headers=bearer(school.t3)))["sections"]["fee_claim_forms"]["latest_at"] is None

    # class teacher declines with a reason; the parent is told; no second review
    declined = _ok(client.put(f"/fee-claim-forms/{form['id']}/decline", headers=bearer(school.t1),
                              json={"note": "Amount doesn't match"}))
    assert declined["status"] == "declined" and declined["reviewed_by_name"] == "Teacher 1"
    assert declined["review_note"] == "Amount doesn't match"
    _ok(client.put(f"/fee-claim-forms/{form['id']}/accept", headers=bearer(school.admin), json={}), 409)
    told = _notes(client, kids[1])
    assert told and told[0]["title"].startswith("Fee claim declined")
    assert told[0]["data"]["route"] == "Fees"  # the claim form lives on the Fees screen
    mine = _ok(client.get(f"/student/fee-claims/{form['id']}", headers=bearer(kids[1])))
    assert mine["status"] == "declined"
    assert _ok(client.get("/updates", headers=bearer(kids[1])))["sections"]["fees"]["latest_at"]

    # a fresh form, accepted by a manager (no fee linked: nothing else changes)
    again = _ok(_send(client, kids[1], purpose="Term 2 tuition", amount="2000"), 201)
    accepted = _ok(client.put(f"/fee-claim-forms/{again['id']}/accept", headers=bearer(school.admin),
                              json={"note": "Received"}))
    assert accepted["status"] == "accepted" and accepted["fee_marked_paid"] is False
    assert accepted["transaction_reference"] is None
    assert _ok(client.get("/fee-claim-forms?status_filter=accepted", headers=bearer(school.t1)))[0]["id"] == again["id"]


def test_a_form_for_a_fee_can_mark_it_paid_and_old_proofs_still_work(client, school, kids):
    a = bearer(school.admin)
    fee = _ok(client.post("/fees", headers=a, json={"student_id": school.s3, "title": "Term fee", "amount": 1200}),
              201)["id"]
    form = _ok(_send(client, kids[3], fee_id=fee, amount="1200",
                     file=("shot.png", png_bytes(), "image/png")), 201)
    assert form["purpose"] == "Term fee" and form["fee_title"] == "Term fee" and form["attachment_type"] == "image"
    _ok(_send(client, kids[3], fee_id=fee, amount="1200"), 409)  # one waiting form per fee
    _ok(_send(client, kids[2], fee_id=fee, amount="1200"), 404)  # not their fee

    # Class 1 — B has no class teacher: only managers review it
    assert _ids(client, school.t1) == []
    done = _ok(client.put(f"/fee-claim-forms/{form['id']}/accept", headers=bearer(login(client, "coordinator")),
                          json={}))
    assert done["fee_marked_paid"] is True and done["transaction_reference"].startswith("TXN")
    fees = _ok(client.get("/student/fees", headers=bearer(kids[3])))
    assert next(f for f in fees["records"] if f["id"] == fee)["status"] == "paid"
    _ok(_send(client, kids[3], fee_id=fee, amount="1200"), 409)  # already paid

    # the reviewer may leave the fee unpaid (e.g. part payment)
    fee2 = _ok(client.post("/fees", headers=a, json={"student_id": school.s1, "title": "Bus fee", "amount": 900}),
               201)["id"]
    part = _ok(_send(client, kids[1], fee_id=fee2, amount="400"), 201)
    kept = _ok(client.put(f"/fee-claim-forms/{part['id']}/accept", headers=bearer(school.t1),
                          json={"mark_fee_paid": False}))
    assert kept["fee_marked_paid"] is False and kept["fee_status"] == "pending"

    # the old per-fee proof flow is untouched and separate
    old = _ok(client.post(f"/fees/{fee2}/claims", headers=bearer(kids[1]), data={"method": "upi"},
                          files={"file": ("p.png", png_bytes(), "image/png")}), 201)
    assert [c["id"] for c in _ok(client.get("/fees/claims", headers=a))] == [old["id"]]
    _ok(_send(client, kids[1], fee_id=fee2, amount="500"), 409)  # its proof is under review
    _ok(client.put(f"/fees/claims/{old['id']}/approve", headers=a, json={}))
    assert set(_ids(client, school.admin)) == {form["id"], part["id"]}


def test_form_checks_and_withdraw(client, school, kids):
    tomorrow = school_today() + timedelta(days=1)
    _ok(_send(client, kids[1], purpose="x", paid_on=tomorrow), 400)
    _ok(_send(client, kids[1], purpose="x", amount="0"), 400)
    _ok(_send(client, kids[1]), 400)  # no fee and no purpose
    _ok(_send(client, kids[1], purpose="x", file=("a.txt", b"hello", "text/plain")), 415)
    _ok(_send(client, kids[1], purpose="x", method="bitcoin"), 422)
    _ok(_send(client, school.t1, purpose="x"), 403)  # only parents send forms
    _ok(client.get("/fee-claim-forms?status_filter=nope", headers=bearer(school.admin)), 400)

    form = _ok(_send(client, kids[4], purpose="Exam fee", amount="300"), 201)
    assert form["id"] in _ids(client, school.t3)
    gone = _ok(client.post(f"/student/fee-claims/{form['id']}/withdraw", headers=bearer(kids[4])))
    assert gone["status"] == "withdrawn"
    _ok(client.post(f"/student/fee-claims/{form['id']}/withdraw", headers=bearer(kids[4])), 409)
    _ok(client.post(f"/student/fee-claims/{form['id']}/withdraw", headers=bearer(kids[1])), 404)
    assert _ids(client, school.t3) == []  # withdrawn forms leave the reviewers' list
    assert _ids(client, school.t3, "/fee-claim-forms?status_filter=withdrawn") == [form["id"]]
    _ok(client.put(f"/fee-claim-forms/{form['id']}/accept", headers=bearer(school.t3), json={}), 409)
    assert _ok(client.get("/fee-claim-forms/stats", headers=bearer(school.t3)))["pending"] == 0
