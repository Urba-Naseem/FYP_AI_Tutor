"""US-4.3 - Learning path for a student, with an explanation for every step."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.rbac import ensure_course_access, require
from ..db import get_db
from ..models.outcomes import CLO, Unit
from ..models.paths import LearningPath
from ..models.user import User
from ..services import paths as svc
from .registration import require_consent

router = APIRouter(prefix="/api/paths", tags=["paths"])


def path_out(db: Session, p: LearningPath) -> dict:
    sts = svc.steps(db, p)
    units = {u.id: u for u in db.scalars(select(Unit).where(Unit.course_id == p.course_id))}
    clos = {o.id: o for o in db.scalars(select(CLO).where(CLO.course_id == p.course_id))}
    nxt = next((s for s in sts if s.status == "pending"), None)
    all_clos = set(clos)
    covered = {s.clo_id for s in sts}
    return {
        "id": p.id, "course_id": p.course_id, "student_id": p.student_id, "version_no": p.version_no, "status": p.status,
        "trigger": p.trigger, "reason": p.reason, "summary": p.summary, "locked": p.locked, "lock_reason": p.lock_reason,
        "created_at": p.created_at, "restored_from": p.restored_from, "rules_version": svc.RULES_VERSION,
        "covers_all_clos": all_clos <= covered, "missing_clos": sorted(clos[c].code for c in all_clos - covered),
        "progress": {"done": sum(1 for s in sts if s.status in ("done", "skipped")), "total": len(sts)},
        "next_step_id": nxt.id if nxt else None,
        "steps": [{"id": s.id, "position": s.position, "unit_id": s.unit_id, "unit_number": units[s.unit_id].number,
                   "unit_title": units[s.unit_id].title, "clo_id": s.clo_id, "clo_code": clos[s.clo_id].code,
                   "clo_description": clos[s.clo_id].description, "step_type": s.step_type, "label": (s.meta or {}).get("topic_title") or svc.STEP_LABEL.get(s.step_type, s.step_type),
                   "status": s.status, "rationale": s.rationale, "rule": s.rule, "completed_at": s.completed_at, "meta": s.meta}
                  for s in sts],
    }


@router.get("/me/{course_id}")
def my_path(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    ensure_course_access(db, user, course_id)
    p = svc.active_path(db, user.id, course_id)
    if p and not p.locked:
        from ..models.outcomes import Course
        from ..services import topic_planner

        topic_planner.ensure(db, user, db.get(Course, course_id))  # once per course: plan topics if missing
        q = svc.upgrade_to_topics(db, user, course_id)  # paths made before the course had topics get them now
        db.commit()  # keeps newly planned topics even when the path itself is not changed
        p = q
    return path_out(db, p) if p else None


@router.post("/me/{course_id}/generate")
def generate(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn")), _c=Depends(require_consent)):
    """Build a path without a diagnostic (the student skips it, or the course uses the prerequisite result).
    An existing path is not replaced."""
    ensure_course_access(db, user, course_id)
    if svc.active_path(db, user.id, course_id):
        raise HTTPException(409, "You already have a learning path")
    p = svc.generate_path(db, user, course_id, trigger="no_diagnostic")
    db.commit()
    return path_out(db, p)


@router.get("/course/{course_id}/student/{student_id}")
def student_path(course_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(require("reports:read"))):
    ensure_course_access(db, user, course_id)
    p = svc.active_path(db, student_id, course_id)
    return path_out(db, p) if p else None


@router.get("/me/{course_id}/decisions")
def my_decisions(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """US-4.5 - Why my path changed."""
    from ..services.adaptation import decisions

    ensure_course_access(db, user, course_id)
    return decisions(db, user.id, course_id)


@router.get("/course/{course_id}/student/{student_id}/decisions")
def student_decisions(course_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(require("reports:read"))):
    from ..services.adaptation import decisions

    ensure_course_access(db, user, course_id)
    return decisions(db, student_id, course_id)
