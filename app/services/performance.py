"""Performance report for the parent app (Nov 2026): how the student did over
the current academic session, from PUBLISHED exam results only.

GET /student/performance →
    {session, grades, overall, exams[], subjects[], insights}

* exams of the student's class (or all classes) whose start date is inside the
  session (`school_profile.current_session`) and whose results are published;
* a mark counts against its own `max_marks`, else the exam's `max_marks`;
  marks without any maximum are left out of percentages; `AB` (absent) counts
  as 0 of the maximum;
* per-subject marks (exam_results.subject <> '') feed the subjects; an exam
  where the student only has a whole-exam mark (subject '') still counts for
  the exam's and the overall percentage;
* class figures (average, rank, best) compare with the students CURRENTLY in
  the same class (all sections) who have marks — fixed number of queries.
"""
from __future__ import annotations

from app.db import Db
from app.services import school_profile

# CBSE-style 8-point scale (min percentage → grade)
GRADES = ((91, "A1"), (81, "A2"), (71, "B1"), (61, "B2"), (51, "C1"), (41, "C2"), (33, "D"), (0, "E"))


def grade_of(pct: float | None) -> str | None:
    if pct is None:
        return None
    for floor, name in GRADES:
        if pct >= floor - 0.0001:
            return name
    return "E"


def _pct(got: float, top: float) -> float | None:
    return round(got * 100 / top, 1) if top else None


def _rank(scores: dict[int, float], me: int) -> int | None:
    """Competition ranking (1, 2, 2, 4) by percentage, highest first."""
    if me not in scores:
        return None
    mine = scores[me]
    return 1 + sum(1 for v in scores.values() if v > mine + 1e-9)


def _avg(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _empty(session: dict) -> dict:
    return {"session": session, "grades": [{"min": f, "grade": g} for f, g in GRADES],
            "overall": None, "exams": [], "subjects": [], "insights": []}


def report(db: Db, uid: int) -> dict:
    sess = school_profile.current_session(db)
    session = {"label": sess["label"], "start": sess["start"], "end": sess["end"]}
    me = db.one("SELECT class_id, section_id FROM student_profiles WHERE user_id = %s AND org_id = %s",
                (uid, db.org)) or {}
    class_id = me.get("class_id")
    if not class_id:
        return _empty(session)
    exams = db.all(
        "SELECT id, name, start_date, end_date, max_marks FROM exams "
        "WHERE org_id = %s AND (class_id = %s OR class_id IS NULL) AND results_published = 1 "
        "AND start_date BETWEEN %s AND %s ORDER BY start_date, id",
        (db.org, class_id, sess["start"], sess["end"]),
    )
    if not exams:
        return _empty(session)
    exam_ids = [e["id"] for e in exams]
    exam_max = {e["id"]: (float(e["max_marks"]) if e["max_marks"] is not None else None) for e in exams}
    rows = db.all(
        "SELECT r.exam_id, r.student_id, r.subject, r.marks, r.max_marks, r.grade "
        "FROM exam_results r JOIN student_profiles sp ON sp.user_id = r.student_id "
        "JOIN users u ON u.id = r.student_id "
        f"WHERE sp.org_id = %s AND sp.class_id = %s AND u.is_active = 1 "
        f"AND r.exam_id IN ({','.join(['%s'] * len(exam_ids))})",
        (db.org, class_id, *exam_ids),
    )
    order = [r["name"] for r in db.all("SELECT name FROM class_subjects WHERE class_id = %s ORDER BY id",
                                       (class_id,))]

    # (student, exam, subject) → (got, top, absent, raw marks, grade)
    cells: dict[tuple[int, int, str], tuple[float, float, bool, float | None, str | None]] = {}
    for r in rows:
        absent = r["marks"] is None and (r["grade"] or "").upper() == "AB"
        if r["marks"] is None and not absent:
            continue
        top = float(r["max_marks"]) if r["max_marks"] is not None else exam_max.get(r["exam_id"])
        if not top:
            continue
        got = 0.0 if absent else float(r["marks"])
        cells[(r["student_id"], r["exam_id"], r["subject"] or "")] = (
            got, top, absent, None if absent else float(r["marks"]), r["grade"])

    # per student per exam totals (subjects; else the whole-exam mark)
    exam_tot: dict[tuple[int, int], list[float]] = {}
    for (sid, eid, sub), (got, top, *_rest) in cells.items():
        if sub:
            t = exam_tot.setdefault((sid, eid), [0.0, 0.0])
            t[0] += got
            t[1] += top
    for (sid, eid, sub), (got, top, *_rest) in cells.items():
        if not sub and (sid, eid) not in exam_tot:
            exam_tot[(sid, eid)] = [got, top]
    # per student per subject totals over the session
    subj_tot: dict[tuple[int, str], list[float]] = {}
    for (sid, _eid, sub), (got, top, *_rest) in cells.items():
        if sub:
            t = subj_tot.setdefault((sid, sub), [0.0, 0.0])
            t[0] += got
            t[1] += top
    overall_tot: dict[int, list[float]] = {}
    for (sid, _eid), (got, top) in exam_tot.items():
        t = overall_tot.setdefault(sid, [0.0, 0.0])
        t[0] += got
        t[1] += top

    overall_pct = {sid: _pct(g, t) for sid, (g, t) in overall_tot.items() if t}
    mine = overall_tot.get(uid)
    if not mine:
        return _empty(session)

    # ---- exams (chronological)
    out_exams = []
    for e in exams:
        scores = {sid: _pct(g, t) for (sid, eid), (g, t) in exam_tot.items() if eid == e["id"] and t}
        if uid not in scores:
            continue
        got, top = exam_tot[(uid, e["id"])]
        out_exams.append({
            "id": e["id"], "name": e["name"], "start_date": e["start_date"], "end_date": e["end_date"],
            "obtained": got, "max_total": top, "percentage": scores[uid], "grade": grade_of(scores[uid]),
            "class_average": _avg(scores.values()), "class_highest": max(scores.values()),
            "rank": _rank(scores, uid), "class_size": len(scores),
        })

    # ---- subjects (class order, then any other subject the student has marks in)
    names = list(order)
    for (sid, sub) in subj_tot:
        if sid == uid and sub not in names:
            names.append(sub)
    out_subjects = []
    for sub in names:
        if (uid, sub) not in subj_tot:
            continue
        got, top = subj_tot[(uid, sub)]
        pct = _pct(got, top)
        scores = {sid: _pct(g, t) for (sid, s), (g, t) in subj_tot.items() if s == sub and t}
        history = []
        for e in exams:
            c = cells.get((uid, e["id"], sub))
            if not c:
                continue
            g, t, absent, raw, grade = c
            class_e = [_pct(cg, ct) for (sid, eid, s), (cg, ct, *_r) in cells.items() if eid == e["id"] and s == sub]
            history.append({"exam_id": e["id"], "exam": e["name"], "marks": raw, "max_marks": t,
                            "absent": absent, "percentage": _pct(g, t), "grade": grade or grade_of(_pct(g, t)),
                            "class_average": _avg(class_e)})
        trend = (round(history[-1]["percentage"] - history[0]["percentage"], 1)
                 if len(history) >= 2 and history[-1]["percentage"] is not None
                 and history[0]["percentage"] is not None else None)
        avg = _avg(scores.values())
        out_subjects.append({
            "subject": sub, "obtained": got, "max_total": top, "percentage": pct, "grade": grade_of(pct),
            "class_average": avg, "class_highest": max(scores.values()) if scores else None,
            "vs_class": round(pct - avg, 1) if pct is not None and avg is not None else None,
            "rank": _rank(scores, uid), "class_size": len(scores), "trend": trend, "exams": history,
        })

    pct = overall_pct[uid]
    class_avg = _avg(overall_pct.values())
    overall = {
        "obtained": mine[0], "max_total": mine[1], "percentage": pct, "grade": grade_of(pct),
        "rank": _rank(overall_pct, uid), "class_size": len(overall_pct), "class_average": class_avg,
        "vs_class": round(pct - class_avg, 1) if pct is not None and class_avg is not None else None,
        "exams_count": len(out_exams), "subjects_count": len(out_subjects),
    }
    return {**_empty(session), "overall": overall, "exams": out_exams, "subjects": out_subjects,
            "insights": _insights(overall, out_subjects, out_exams)}


def _insights(overall: dict, subjects: list[dict], exams: list[dict]) -> list[dict]:
    """A few plain-language highlights; the app picks icons by `kind`."""
    out: list[dict] = []
    ranked = [s for s in subjects if s["percentage"] is not None]
    if ranked:
        best = max(ranked, key=lambda s: s["percentage"])
        out.append({"kind": "strongest", "title": "Strongest subject",
                    "text": f"{best['subject']} ({best['percentage']:g}%)", "subject": best["subject"]})
        if len(ranked) > 1:
            low = min(ranked, key=lambda s: s["percentage"])
            if low["subject"] != best["subject"]:
                out.append({"kind": "focus", "title": "Focus area",
                            "text": f"{low['subject']} ({low['percentage']:g}%)", "subject": low["subject"]})
    if overall.get("vs_class") is not None and overall["class_size"] > 1:
        d = overall["vs_class"]
        out.append({"kind": "above_average" if d >= 0 else "below_average",
                    "title": "Above class average" if d >= 0 else "Below class average",
                    "text": f"{'+' if d >= 0 else ''}{d:g}% overall vs the class"})
    improving = [s for s in subjects if s["trend"] is not None and s["trend"] > 0]
    if improving:
        top = max(improving, key=lambda s: s["trend"])
        out.append({"kind": "improved", "title": "Most improved",
                    "text": f"{top['subject']} (+{top['trend']:g}% since the first exam)", "subject": top["subject"]})
    if len(exams) >= 2:
        d = round(exams[-1]["percentage"] - exams[0]["percentage"], 1)
        out.append({"kind": "trend_up" if d >= 0 else "trend_down", "title": "Overall trend",
                    "text": f"{'+' if d >= 0 else ''}{d:g}% from {exams[0]['name']} to {exams[-1]['name']}"})
    return out
