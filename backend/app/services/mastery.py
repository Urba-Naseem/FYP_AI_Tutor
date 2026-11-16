"""NB-3 - Mastery calculation, evidence versioning and recalculation.

RULE (best-of-last-3/v1)
  * Evidence = scored formative work for one CLO (quiz or reteach attempt),
    built only from *approved* items. Diagnostic results set readiness, not mastery.
  * Mastery % = the best score among the student's 3 most recent active
    evidence records for that CLO.
  * Attained when mastery % >= the attainment criterion (US-1.4).
  * Which criterion version applies: the one in force when the most recent
    counted evidence was recorded (decisions are reproducible). An explicit
    recalculation in "current_criteria" mode re-applies today's criterion.
  * Evidence is never edited: an amendment (e.g. upheld appeal) creates a new
    version and supersedes the old one, then mastery is recalculated.
  * Every calculation is stored as a MasteryRecord (history), with the
    evidence ids, criterion id/version and rule version it used.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..db import utcnow
from ..models.mastery import MasteryEvidence, MasteryRecord
from ..models.outcomes import CLO
from . import criteria as crit

RULE_VERSION = "best-of-last-3/v1"
WINDOW = 3
SOURCES = ("quiz", "reteach")

POLICY = {
    "rule_version": RULE_VERSION,
    "window": WINDOW,
    "summary": "Mastery is the best score among the 3 most recent attempts for a CLO; a CLO is attained when mastery "
               "reaches the attainment criterion (default 60%).",
    "evidence_sources": list(SOURCES),
    "criterion_version": "The criterion in force when the latest counted attempt was recorded. Recalculation can "
                         "re-apply the current criterion and is always logged.",
    "amendments": "Evidence is versioned: amendments supersede the old record and trigger recalculation.",
}


def active_evidence(db: Session, student_id: int, clo_id: int) -> list[MasteryEvidence]:
    return db.scalars(
        select(MasteryEvidence)
        .where(MasteryEvidence.student_id == student_id, MasteryEvidence.clo_id == clo_id,
               MasteryEvidence.status == "active", MasteryEvidence.source.in_(SOURCES))
        .order_by(MasteryEvidence.recorded_at.desc(), MasteryEvidence.id.desc())
    ).all()


def current(db: Session, student_id: int, clo_id: int) -> MasteryRecord | None:
    return db.scalar(select(MasteryRecord).where(MasteryRecord.student_id == student_id, MasteryRecord.clo_id == clo_id,
                                                 MasteryRecord.is_current.is_(True)))


def calculate(db: Session, student_id: int, clo_id: int, trigger: str, actor=None, use_current_criteria: bool = False,
              note: str | None = None) -> MasteryRecord | None:
    ev = active_evidence(db, student_id, clo_id)[:WINDOW]
    if not ev:
        return None
    clo = db.get(CLO, clo_id)
    at: datetime | None = None if use_current_criteria else max(e.recorded_at for e in ev)
    c = crit.resolve(db, clo, at)
    pct = round(max(e.score_pct for e in ev), 2)
    prev = current(db, student_id, clo_id)
    if prev:
        prev.is_current = False
    rec = MasteryRecord(
        student_id=student_id, course_id=clo.course_id, clo_id=clo_id, mastery_pct=pct, attained=pct >= c["threshold"],
        threshold=c["threshold"], criterion_id=c["criterion_id"], criterion_version=c["version_no"],
        rule_version=RULE_VERSION, evidence_ids=[e.id for e in ev], attempts_counted=len(ev), trigger=trigger,
        note=note, calculated_by=getattr(actor, "id", None), is_current=True,
    )
    db.add(rec)
    db.flush()
    changed = prev is None or prev.attained != rec.attained or prev.mastery_pct != rec.mastery_pct
    record(db, actor, "mastery.calculated", "mastery_record", rec.id,
           {"student_id": student_id, "clo_id": clo_id, "mastery_pct": pct, "attained": rec.attained,
            "threshold": c["threshold"], "criterion": f"{c['scope_key']} v{c['version_no']}", "trigger": trigger,
            "previous": {"mastery_pct": prev.mastery_pct, "attained": prev.attained} if prev else None,
            "changed": changed},
           course_id=clo.course_id, category="learning")
    return rec


def add_evidence(db: Session, student, clo_id: int, source: str, source_ref: str, items: list[dict]) -> tuple[MasteryEvidence, MasteryRecord]:
    """items: [{item_id, item_version, correct: bool}] - all for the same CLO."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if not items:
        raise ValueError("evidence needs at least one item")
    correct = sum(1 for i in items if i["correct"])
    clo = db.get(CLO, clo_id)
    e = MasteryEvidence(student_id=student.id, course_id=clo.course_id, clo_id=clo_id, source=source,
                        source_ref=source_ref, correct=correct, total=len(items),
                        score_pct=round(100.0 * correct / len(items), 2), items=items, recorded_at=utcnow())
    db.add(e)
    db.flush()
    rec = calculate(db, student.id, clo_id, trigger="attempt", actor=student)
    return e, rec


def amend_evidence(db: Session, evidence_id: int, new_correct: int, reason: str, actor) -> tuple[MasteryEvidence, MasteryRecord]:
    old = db.get(MasteryEvidence, evidence_id)
    if old is None or old.status != "active":
        raise ValueError("Only active evidence can be amended")
    if not 0 <= new_correct <= old.total:
        raise ValueError(f"Score must be between 0 and {old.total}")
    old.status = "superseded"
    new = MasteryEvidence(student_id=old.student_id, course_id=old.course_id, clo_id=old.clo_id, source=old.source,
                          source_ref=old.source_ref, correct=new_correct, total=old.total,
                          score_pct=round(100.0 * new_correct / old.total, 2), items=old.items,
                          recorded_at=old.recorded_at, version_no=old.version_no + 1, supersedes_id=old.id,
                          status="active", change_reason=reason, changed_by=actor.id)
    db.add(new)
    db.flush()
    record(db, actor, "mastery.evidence_amended", "mastery_evidence", new.id,
           {"supersedes": old.id, "before": f"{old.correct}/{old.total}", "after": f"{new_correct}/{old.total}", "reason": reason},
           course_id=old.course_id)
    rec = calculate(db, old.student_id, old.clo_id, trigger="amendment", actor=actor, note=reason)
    return new, rec


def recalculate_course(db: Session, course_id: int, actor, mode: str, reason: str) -> dict:
    use_current = mode == "current_criteria"
    pairs = {(e.student_id, e.clo_id) for e in db.scalars(select(MasteryEvidence).where(
        MasteryEvidence.course_id == course_id, MasteryEvidence.status == "active"))}
    changed = 0
    for sid, clo_id in sorted(pairs):
        before = current(db, sid, clo_id)
        b = (before.mastery_pct, before.attained) if before else None
        rec = calculate(db, sid, clo_id, trigger="recalculation", actor=actor, use_current_criteria=use_current, note=reason)
        if rec and b != (rec.mastery_pct, rec.attained):
            changed += 1
    record(db, actor, "mastery.recalculated", "course", course_id,
           {"mode": mode, "reason": reason, "records": len(pairs), "changed": changed}, course_id=course_id)
    return {"records": len(pairs), "changed": changed, "mode": mode}
