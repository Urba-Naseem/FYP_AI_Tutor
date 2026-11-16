"""US-6.4 - Flag repeated difficulty so learners do not become trapped.

Automatic escalation:
  * reteach_exhausted  - all reteach attempts used and the CLO is still below mastery (US-6.3)
  * repeated_failure   - REPEATED_FAILURES or more consecutive attempts below mastery on one CLO
An open escalation is routed to the review queue of whoever currently teaches the course.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..models.escalation import Escalation
from . import ai_gateway

REPEATED_FAILURES = 3


def open_escalation(db: Session, student_id: int, clo_id: int | None) -> Escalation | None:
    return db.scalar(select(Escalation).where(Escalation.student_id == student_id, Escalation.clo_id == clo_id,
                                              Escalation.status.in_(["open", "acknowledged"])))


def raise_escalation(db: Session, student, course_id: int, clo, trigger: str, summary: str, created_by=None) -> Escalation:
    existing = open_escalation(db, student.id, clo.id if clo else None)
    if existing:
        if trigger == "reteach_exhausted" and existing.trigger != trigger:
            existing.trigger, existing.summary = trigger, summary  # more serious - update the reason
        return existing
    e = Escalation(student_id=student.id, course_id=course_id, clo_id=clo.id if clo else None, trigger=trigger,
                   summary=summary, created_by=getattr(created_by, "id", None))
    db.add(e)
    db.flush()
    ai_gateway.route_review(db, "escalation", "escalation", e.id, course_id,
                            f"{student.full_name}: {summary}", created_by or student)
    record(db, created_by or student, "escalation.raised", "escalation", e.id,
           {"student_id": student.id, "trigger": trigger, "clo_id": e.clo_id, "summary": summary}, course_id=course_id)
    return e


def check_escalation(db: Session, student, course_id: int, clo, failures: int, rec) -> Escalation | None:
    if not rec.attained and failures >= REPEATED_FAILURES:
        return raise_escalation(db, student, course_id, clo, "repeated_failure",
                                f"{clo.code}: {failures} attempts in a row below {rec.threshold:.0f}% (mastery {rec.mastery_pct:.0f}%).")
    return None
