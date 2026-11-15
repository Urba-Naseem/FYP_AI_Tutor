"""US-4.1 - Adaptive item selection and readiness scoring.

Selection (per sitting):
  * Every CLO is assessed: the next question comes from the CLO with the fewest
    answers so far (ties: CLO order), so coverage is balanced.
  * Within a CLO, difficulty adapts: start at medium (2); after a correct answer
    the next item for that CLO is harder, after a wrong one easier.
  * A CLO is finished after `items_per_clo` answers (or when its bank is exhausted).

Readiness per CLO = difficulty-weighted % correct:
  >= 70 ready, >= 40 developing, otherwise not_ready.
"""
from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.diagnostic import DiagnosticAttempt, DiagnosticResponse, ReadinessResult
from ..models.items import Item
from ..models.outcomes import CLO

READY, DEVELOPING = 70.0, 40.0


def level_for(score: float) -> str:
    return "ready" if score >= READY else "developing" if score >= DEVELOPING else "not_ready"


def _responses(db: Session, attempt: DiagnosticAttempt) -> list[DiagnosticResponse]:
    return db.scalars(select(DiagnosticResponse).where(DiagnosticResponse.attempt_id == attempt.id).order_by(DiagnosticResponse.seq)).all()


def next_item(db: Session, attempt: DiagnosticAttempt) -> Item | None:
    clos = db.scalars(select(CLO).where(CLO.course_id == attempt.course_id).order_by(CLO.code)).all()
    resp = _responses(db, attempt)
    asked = {r.item_id for r in resp}
    candidates = []
    for clo in clos:
        mine = [r for r in resp if r.clo_id == clo.id]
        if len(mine) >= attempt.items_per_clo:
            continue
        pool = db.scalars(select(Item).where(Item.clo_id == clo.id, Item.purpose == "diagnostic", Item.status == "approved")).all()
        pool = [i for i in pool if i.id not in asked]
        if not pool:
            continue
        target = 2
        if mine:
            last = mine[-1]
            target = min(3, last.difficulty + 1) if last.correct else max(1, last.difficulty - 1)
        # Same difficulty rules for everyone, but a different question for each student: among the questions closest
        # to the target difficulty, the choice is a stable per-student shuffle (not the lowest id for all).
        rnd = random.Random(f"{attempt.student_id}:{clo.id}:{len(mine)}")
        shuffle = {i.id: rnd.random() for i in pool}
        pool.sort(key=lambda i: (abs(i.difficulty - target), shuffle[i.id]))
        candidates.append((len(mine), clo.code, pool[0]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2]


def total_planned(db: Session, attempt: DiagnosticAttempt) -> int:
    n = 0
    for clo in db.scalars(select(CLO).where(CLO.course_id == attempt.course_id)):
        avail = len(db.scalars(select(Item.id).where(Item.clo_id == clo.id, Item.purpose == "diagnostic", Item.status == "approved")).all())
        n += min(avail, attempt.items_per_clo)
    return n


def score(db: Session, attempt: DiagnosticAttempt) -> list[ReadinessResult]:
    out = []
    resp = _responses(db, attempt)
    for clo in db.scalars(select(CLO).where(CLO.course_id == attempt.course_id).order_by(CLO.code)):
        mine = [r for r in resp if r.clo_id == clo.id]
        weight = sum(r.difficulty for r in mine)
        got = sum(r.difficulty for r in mine if r.correct)
        pct = round(100.0 * got / weight, 1) if weight else 0.0
        rr = ReadinessResult(attempt_id=attempt.id, student_id=attempt.student_id, course_id=attempt.course_id, clo_id=clo.id,
                             score_pct=pct, level=level_for(pct), answered=len(mine), correct=sum(1 for r in mine if r.correct),
                             detail={"difficulties": [r.difficulty for r in mine], "correct": [r.correct for r in mine]})
        db.add(rr)
        out.append(rr)
    db.flush()
    return out


def latest_readiness(db: Session, student_id: int, course_id: int) -> dict[int, ReadinessResult]:
    att = db.scalar(select(DiagnosticAttempt).where(DiagnosticAttempt.student_id == student_id, DiagnosticAttempt.course_id == course_id,
                                                    DiagnosticAttempt.status == "completed").order_by(DiagnosticAttempt.completed_at.desc()).limit(1))
    if not att:
        return {}
    return {r.clo_id: r for r in db.scalars(select(ReadinessResult).where(ReadinessResult.attempt_id == att.id))}


# ---- when is a diagnostic needed? (semester / prerequisite rule) ---------------------------------------------
# Semester 1 courses: every student takes the diagnostic.
# Semester 2 onwards, a course with prerequisites: no new test. The learning path is built from the student's FINAL
#   result in the prerequisite course(s): the average of their current mastery over all the prerequisite's CLOs
#   (a CLO with no mastery record counts as 0%), placed on the same ready / developing / not ready scale.
# A course without prerequisites, or when any prerequisite has no final result for this student (not completed, or
#   completed without any mastery evidence): the diagnostic is taken as before (fallback).
# A diagnostic the student did take always wins over the prerequisite result.
from dataclasses import dataclass  # noqa: E402


@dataclass
class PrereqReadiness:
    """Readiness for one CLO derived from prerequisite results (same fields the path rules read)."""
    clo_id: int
    score_pct: float
    level: str


def prerequisite_results(db: Session, student_id: int, course) -> tuple[list[dict], list[str]]:
    """([{code, title, course_id, score_pct}], [prerequisite codes with no final result])."""
    from ..models.enrollment import Enrollment
    from ..models.outcomes import Course
    from . import mastery

    found, missing = [], []
    for code in course.prerequisites or []:
        e = db.scalar(select(Enrollment).join(Course, Course.id == Enrollment.course_id)
                      .where(Enrollment.student_id == student_id, Enrollment.status == "completed", Course.code == code)
                      .order_by(Enrollment.completed_at.desc()).limit(1))
        pre = db.get(Course, e.course_id) if e else None
        clos = db.scalars(select(CLO).where(CLO.course_id == pre.id)).all() if pre else []
        recs = [mastery.current(db, student_id, c.id) for c in clos]
        if not pre or not clos or not any(recs):
            missing.append(code)
            continue
        pct = round(sum(r.mastery_pct if r else 0.0 for r in recs) / len(clos), 1)
        found.append({"code": pre.code, "title": pre.title, "course_id": pre.id, "score_pct": pct})
    return found, missing


def diagnostic_mode(db: Session, student_id: int, course) -> dict:
    """{"mode": "test" | "prerequisite", "reason": str, "prerequisites": [...], "score_pct": float | None}."""
    out = {"mode": "test", "reason": "", "prerequisites": [], "score_pct": None}
    if course.semester == 1:
        out["reason"] = "Semester 1 course: every student takes the diagnostic."
        return out
    if not course.prerequisites:
        out["reason"] = "This course has no prerequisite, so you take the diagnostic."
        return out
    found, missing = prerequisite_results(db, student_id, course)
    if missing:
        out["reason"] = (f"No final result was found for the prerequisite {', '.join(missing)}, so you take the "
                         "diagnostic for this course instead.")
        return out
    pct = round(sum(f["score_pct"] for f in found) / len(found), 1)
    names = ", ".join(f"{f['code']} {f['title']}" for f in found)
    return {"mode": "prerequisite", "prerequisites": found, "score_pct": pct,
            "reason": f"No diagnostic needed: your learning path is built from your final result in {names} ({pct:.0f}%)."}


def course_readiness(db: Session, student_id: int, course_id: int) -> tuple[dict, dict]:
    """(readiness per CLO id, info). info["source"] is "diagnostic", "prerequisite" or None (no data)."""
    from ..models.outcomes import Course

    ready = latest_readiness(db, student_id, course_id)
    if ready:
        return ready, {"source": "diagnostic"}
    course = db.get(Course, course_id)
    mode = diagnostic_mode(db, student_id, course) if course else {"mode": "test"}
    if mode["mode"] != "prerequisite":
        return {}, {"source": None}
    pct = mode["score_pct"]
    level = level_for(pct)
    derived = {c.id: PrereqReadiness(c.id, pct, level) for c in db.scalars(select(CLO).where(CLO.course_id == course_id))}
    names = ", ".join(f"{p['code']} {p['title']}" for p in mode["prerequisites"])
    return derived, {"source": "prerequisite", "names": names, "score_pct": pct}
