"""US-6.3 - Reteach below mastery: alternate explanation, new questions, attempt limit."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.rbac import ensure_course_access, require
from ..db import get_db
from ..models.outcomes import CLO
from ..models.quiz import QuizAttempt
from ..models.reteach import ReteachCycle
from ..models.user import User
from ..services import mastery
from ..services import reteach as svc
from .quiz import MIN_ITEMS, attempt_out, pick_items, start_attempt
from .registration import require_consent

router = APIRouter(prefix="/api/reteach", tags=["reteach"])


class QuizIn(BaseModel):
    step_id: int | None = None


def cycle_out(db: Session, c: ReteachCycle) -> dict:
    clo = db.get(CLO, c.clo_id)
    rec = mastery.current(db, c.student_id, c.clo_id)
    open_quiz = db.scalar(select(QuizAttempt).where(QuizAttempt.student_id == c.student_id, QuizAttempt.clo_id == c.clo_id,
                                                    QuizAttempt.kind == "reteach", QuizAttempt.status == "in_progress"))
    fresh = len(pick_items(db, c.student_id, c.clo_id, ("reteach", "formative"), unseen_only=True))
    return {"id": c.id, "clo_id": c.clo_id, "clo_code": clo.code, "clo_description": clo.description, "status": c.status,
            "attempts_used": c.attempts_used, "max_attempts": c.max_attempts, "attempts_left": c.max_attempts - c.attempts_used,
            "explanation_source": c.explanation_source, "explanation_md": c.explanation_md, "rules_version": svc.RULES_VERSION,
            "mastery": {"mastery_pct": rec.mastery_pct, "threshold": rec.threshold, "attained": rec.attained} if rec else None,
            "open_quiz_id": open_quiz.id if open_quiz else None, "fresh_questions": fresh}


@router.get("/{course_id}/clo/{clo_id}")
def open_reteach(course_id: int, clo_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn")),
                 _c=Depends(require_consent)):
    ensure_course_access(db, user, course_id)
    clo = db.get(CLO, clo_id)
    if not clo or clo.course_id != course_id:
        raise HTTPException(404, "Outcome not found")
    cyc = svc.active_cycle(db, user.id, clo_id)
    if not cyc:
        last = db.scalar(select(ReteachCycle).where(ReteachCycle.student_id == user.id, ReteachCycle.clo_id == clo_id)
                         .order_by(ReteachCycle.id.desc()).limit(1))
        rec = mastery.current(db, user.id, clo_id)
        if last and last.status == "exhausted":
            return cycle_out(db, last)  # attempt limit reached: only an instructor intervention reopens it
        if rec and rec.attained:
            if last:
                return cycle_out(db, last)
            raise HTTPException(409, f"{clo.code} is already attained - no reteaching needed")
        if rec is None and not last:
            # reteaching follows a formative quiz below the target; it cannot be opened before one
            raise HTTPException(409, f"Take the formative quiz for {clo.code} first - reteaching starts when a quiz is below the target")
        cyc = svc.open_cycle(db, user, course_id, clo)
    db.commit()
    return cycle_out(db, cyc)


@router.post("/cycles/{cid}/quiz")
def reteach_quiz(cid: int, body: QuizIn, db: Session = Depends(get_db), user: User = Depends(require("learn")),
                 _c=Depends(require_consent)):
    cyc = db.get(ReteachCycle, cid)
    if not cyc or cyc.student_id != user.id:
        raise HTTPException(404, "Reteach not found")
    if cyc.status != "active":
        raise HTTPException(409, f"This reteach cycle is {cyc.status}")
    existing = db.scalar(select(QuizAttempt).where(QuizAttempt.student_id == user.id, QuizAttempt.clo_id == cyc.clo_id,
                                                   QuizAttempt.status == "in_progress"))
    if existing:
        return attempt_out(db, existing)
    if cyc.attempts_used >= cyc.max_attempts:
        raise HTTPException(409, "No reteach attempts left")
    from ..services import paths as path_svc

    if (why := path_svc.gate(db, user.id, cyc.course_id, cyc.clo_id, ("reteach",))):
        raise HTTPException(409, why)
    unseen = pick_items(db, user.id, cyc.clo_id, ("reteach", "formative"), unseen_only=True)
    if len(unseen) < MIN_ITEMS:
        raise HTTPException(409, "There are not enough new questions for this outcome yet - please ask your instructor to add more")
    a = start_attempt(db, user, cyc.course_id, cyc.clo_id, "reteach", body.step_id, ("reteach", "formative"), unseen_only=True)
    cyc.attempts_used += 1
    db.commit()
    return attempt_out(db, a)
