"""US-4.4 - Override, lock and restore learning paths (faculty control over automatic decisions)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..core.rbac import ensure_course_access, require
from ..db import get_db
from ..models.outcomes import CLO
from ..models.paths import LearningPath, PathStep
from ..models.user import User
from ..services import paths as svc
from .paths import path_out

router = APIRouter(prefix="/api/paths/course/{course_id}/student/{student_id}", tags=["path-control"])
STEP_TYPES = ("remedial", "learn", "practice", "review", "reteach")


class ReasonIn(BaseModel):
    reason: str


class StepIn(BaseModel):
    step_id: int | None = None  # keep an existing step (with its status)
    clo_id: int | None = None  # or add a new one
    step_type: str | None = None


class EditIn(BaseModel):
    steps: list[StepIn]
    reason: str


def _staff_path(db: Session, user: User, course_id: int, student_id: int) -> LearningPath:
    ensure_course_access(db, user, course_id, write=True)
    p = svc.active_path(db, student_id, course_id)
    if not p:
        raise HTTPException(404, "The learner has no learning path yet")
    return p


def _reason(r: str):
    if len(r.strip()) < 10:
        raise HTTPException(422, "A reason of at least 10 characters is required")


@router.post("/lock")
def lock(course_id: int, student_id: int, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("paths:manage"))):
    p = _staff_path(db, user, course_id, student_id)
    _reason(body.reason)
    if p.locked:
        raise HTTPException(409, "Path is already locked")
    p.locked, p.lock_reason, p.locked_by = True, body.reason, user.id
    record(db, user, "path.locked", "learning_path", p.id, {"student_id": student_id, "reason": body.reason}, course_id=course_id)
    db.commit()
    return path_out(db, p)


@router.post("/unlock")
def unlock(course_id: int, student_id: int, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("paths:manage"))):
    p = _staff_path(db, user, course_id, student_id)
    _reason(body.reason)
    if not p.locked:
        raise HTTPException(409, "Path is not locked")
    p.locked, p.lock_reason, p.locked_by = False, None, None
    record(db, user, "path.unlocked", "learning_path", p.id, {"student_id": student_id, "reason": body.reason}, course_id=course_id)
    db.commit()
    return path_out(db, p)


@router.put("/steps")
def edit_steps(course_id: int, student_id: int, body: EditIn, db: Session = Depends(get_db), user: User = Depends(require("paths:manage"))):
    """Override: reorder, remove or add steps. Creates a new version; every CLO must stay covered."""
    p = _staff_path(db, user, course_id, student_id)
    _reason(body.reason)
    current = {s.id: s for s in svc.steps(db, p)}
    clos = {o.id: o for o in db.scalars(select(CLO).where(CLO.course_id == course_id))}
    new = []
    for st in body.steps:
        if st.step_id:
            s = current.get(st.step_id)
            if not s:
                raise HTTPException(422, f"Step {st.step_id} is not in the current path")
            new.append(svc.step_dict(s))
        else:
            clo = clos.get(st.clo_id)
            if not clo or st.step_type not in STEP_TYPES:
                raise HTTPException(422, "New steps need a CLO of this course and a valid step type")
            new.append(dict(unit_id=clo.unit_id, clo_id=clo.id, step_type=st.step_type, status="pending", rule="I-OVERRIDE",
                            rationale=f"Added by your instructor: {body.reason}", meta={"override": True}))
    missing = sorted(clos[c].code for c in set(clos) - {s["clo_id"] for s in new})
    if missing:
        raise HTTPException(422, f"The path must still cover every CLO (missing: {', '.join(missing)})")
    np_ = svc.save_version(db, user, student_id, course_id, new, p.summary, "override", reason=f"Instructor override: {body.reason}")
    record(db, user, "path.overridden", "learning_path", np_.id,
           {"student_id": student_id, "from_version": p.version_no, "to_version": np_.version_no, "reason": body.reason},
           course_id=course_id)
    db.commit()
    return path_out(db, np_)


@router.get("/versions")
def versions(course_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(require("reports:read"))):
    ensure_course_access(db, user, course_id)
    rows = db.scalars(select(LearningPath).where(LearningPath.student_id == student_id, LearningPath.course_id == course_id)
                      .order_by(LearningPath.version_no.desc())).all()
    return [{"id": r.id, "version_no": r.version_no, "status": r.status, "trigger": r.trigger, "reason": r.reason,
             "locked": r.locked, "created_at": r.created_at, "restored_from": r.restored_from,
             "steps": len(db.scalars(select(PathStep.id).where(PathStep.path_id == r.id)).all())} for r in rows]


@router.post("/restore/{version_no}")
def restore(course_id: int, student_id: int, version_no: int, body: ReasonIn, db: Session = Depends(get_db),
            user: User = Depends(require("paths:manage"))):
    p = _staff_path(db, user, course_id, student_id)
    _reason(body.reason)
    old = db.scalar(select(LearningPath).where(LearningPath.student_id == student_id, LearningPath.course_id == course_id,
                                               LearningPath.version_no == version_no))
    if not old:
        raise HTTPException(404, "Version not found")
    if old.id == p.id:
        raise HTTPException(409, "That version is already the active one")
    np_ = svc.save_version(db, user, student_id, course_id, [svc.step_dict(s) for s in svc.steps(db, old)], old.summary, "restore",
                           reason=f"Restored version {version_no}: {body.reason}", restored_from=version_no)
    record(db, user, "path.restored", "learning_path", np_.id,
           {"student_id": student_id, "restored_version": version_no, "reason": body.reason}, course_id=course_id)
    db.commit()
    return path_out(db, np_)


@router.post("/regenerate")
def regenerate(course_id: int, student_id: int, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(require("paths:manage"))):
    """Rebuild from the rules (a lock, if any, is kept)."""
    p = _staff_path(db, user, course_id, student_id)
    _reason(body.reason)
    step_dicts, summary, _trig = svc.plan(db, student_id, course_id)
    np_ = svc.save_version(db, user, student_id, course_id, step_dicts, summary, "override", reason=f"Rebuilt from rules: {body.reason}")
    record(db, user, "path.regenerated", "learning_path", np_.id, {"student_id": student_id, "reason": body.reason, "from": p.version_no},
           course_id=course_id)
    db.commit()
    return path_out(db, np_)
