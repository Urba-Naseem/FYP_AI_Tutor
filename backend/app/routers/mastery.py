"""NB-3 - Mastery policy, explanations and controlled recalculation."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.rbac import ensure_course_access, require
from ..db import get_db
from ..models.mastery import MasteryEvidence, MasteryRecord
from ..models.outcomes import CLO, Course
from ..models.user import User
from ..services import mastery as svc

router = APIRouter(prefix="/api/mastery", tags=["mastery"])


class RecalcIn(BaseModel):
    course_id: int
    mode: str = "reproduce"  # reproduce | current_criteria
    reason: str


def rec_out(r: MasteryRecord) -> dict:
    return {"id": r.id, "clo_id": r.clo_id, "mastery_pct": r.mastery_pct, "attained": r.attained, "threshold": r.threshold,
            "criterion_id": r.criterion_id, "criterion_version": r.criterion_version, "rule_version": r.rule_version,
            "evidence_ids": r.evidence_ids, "attempts_counted": r.attempts_counted, "trigger": r.trigger, "note": r.note,
            "calculated_at": r.calculated_at, "is_current": r.is_current}


def ev_out(e: MasteryEvidence) -> dict:
    return {"id": e.id, "clo_id": e.clo_id, "source": e.source, "source_ref": e.source_ref, "correct": e.correct,
            "total": e.total, "score_pct": e.score_pct, "recorded_at": e.recorded_at, "version_no": e.version_no,
            "status": e.status, "supersedes_id": e.supersedes_id, "change_reason": e.change_reason}


def student_course_mastery(db: Session, student_id: int, course_id: int) -> dict:
    clos = db.scalars(select(CLO).where(CLO.course_id == course_id).order_by(CLO.code)).all()
    out = []
    for o in clos:
        cur = svc.current(db, student_id, o.id)
        hist = db.scalars(select(MasteryRecord).where(MasteryRecord.student_id == student_id, MasteryRecord.clo_id == o.id)
                          .order_by(MasteryRecord.id.desc())).all()
        ev = db.scalars(select(MasteryEvidence).where(MasteryEvidence.student_id == student_id, MasteryEvidence.clo_id == o.id)
                        .order_by(MasteryEvidence.id.desc())).all()
        out.append({"clo_id": o.id, "clo_code": o.code, "description": o.description,
                    "current": rec_out(cur) if cur else None, "history": [rec_out(h) for h in hist],
                    "evidence": [ev_out(e) for e in ev]})
    return {"course_id": course_id, "student_id": student_id, "clos": out, "policy": svc.POLICY}


@router.get("/policy")
def policy(_=Depends(require("reports:read", "learn", "criteria:manage"))):
    return svc.POLICY


@router.post("/recalculate")
def recalculate(body: RecalcIn, db: Session = Depends(get_db), user: User = Depends(require("mastery:recalc"))):
    if body.mode not in ("reproduce", "current_criteria"):
        raise HTTPException(422, "mode must be 'reproduce' or 'current_criteria'")
    if len(body.reason.strip()) < 5:
        raise HTTPException(422, "A reason is required")
    if not db.get(Course, body.course_id):
        raise HTTPException(404, "Course not found")
    res = svc.recalculate_course(db, body.course_id, user, body.mode, body.reason)
    db.commit()
    return res


@router.get("/course/{course_id}/student/{student_id}")
def staff_view(course_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(require("reports:read"))):
    ensure_course_access(db, user, course_id)
    return student_course_mastery(db, student_id, course_id)


@router.get("/me/{course_id}")
def my_mastery(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    ensure_course_access(db, user, course_id)
    return student_course_mastery(db, user.id, course_id)
