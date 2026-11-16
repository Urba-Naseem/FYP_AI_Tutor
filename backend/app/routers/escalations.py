"""US-6.4 - Handle escalated learners: review flags and record interventions / overrides with reasons."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..core.rbac import accessible_course_ids, ensure_course_access, require
from ..db import get_db, utcnow
from ..models.enrollment import Enrollment
from ..models.escalation import Escalation, Intervention
from ..models.mastery import MasteryRecord
from ..models.outcomes import CLO
from ..models.reteach import ReteachCycle
from ..models.user import User
from ..services import ai_gateway
from ..services import mastery as mastery_svc
from ..services import paths as path_svc
from ..services.escalation import raise_escalation

router = APIRouter(prefix="/api", tags=["escalations"])
ACTIONS = ("note", "meeting", "reset_attempts", "override_advance", "mastery_override")
MIN_REASON = 10


class RaiseIn(BaseModel):
    student_id: int
    course_id: int
    clo_id: int | None = None
    summary: str


class InterventionIn(BaseModel):
    student_id: int
    course_id: int
    action: str
    reason: str
    clo_id: int | None = None
    escalation_id: int | None = None
    attained: bool | None = None  # for mastery_override
    resolve: bool = False


class ResolveIn(BaseModel):
    resolution: str


def esc_out(db: Session, e: Escalation) -> dict:
    st = db.get(User, e.student_id)
    clo = db.get(CLO, e.clo_id) if e.clo_id else None
    ivs = db.scalars(select(Intervention).where(Intervention.escalation_id == e.id).order_by(Intervention.id)).all()
    return {"id": e.id, "student": {"id": st.id, "name": st.full_name}, "course_id": e.course_id,
            "clo_id": e.clo_id, "clo_code": clo.code if clo else None, "trigger": e.trigger, "summary": e.summary,
            "status": e.status, "created_at": e.created_at, "resolved_at": e.resolved_at, "resolution": e.resolution,
            "interventions": [iv_out(db, i) for i in ivs]}


def iv_out(db: Session, i: Intervention) -> dict:
    inst = db.get(User, i.instructor_id)
    return {"id": i.id, "action": i.action, "reason": i.reason, "details": i.details, "at": i.at,
            "instructor": inst.full_name if inst else None, "clo_id": i.clo_id, "escalation_id": i.escalation_id}


def _student_in_course(db: Session, student_id: int, course_id: int) -> User:
    st = db.get(User, student_id)
    if not st or not db.scalar(select(Enrollment).where(Enrollment.student_id == student_id, Enrollment.course_id == course_id)):
        raise HTTPException(404, "Student is not enrolled in this course")
    return st


@router.get("/escalations")
def list_escalations(course_id: int | None = None, status: str | None = None, student_id: int | None = None,
                     db: Session = Depends(get_db), user: User = Depends(require("interventions:manage"))):
    q = select(Escalation).order_by(Escalation.created_at.desc())
    if course_id:
        q = q.where(Escalation.course_id == course_id)
    if status:
        q = q.where(Escalation.status == status)
    if student_id:
        q = q.where(Escalation.student_id == student_id)
    allowed = accessible_course_ids(db, user)
    return [esc_out(db, e) for e in db.scalars(q) if allowed is None or e.course_id in allowed]


@router.post("/escalations", status_code=201)
def manual_raise(body: RaiseIn, db: Session = Depends(get_db), user: User = Depends(require("interventions:manage"))):
    ensure_course_access(db, user, body.course_id, write=True)
    st = _student_in_course(db, body.student_id, body.course_id)
    if len(body.summary.strip()) < MIN_REASON:
        raise HTTPException(422, "Describe the concern")
    clo = db.get(CLO, body.clo_id) if body.clo_id else None
    e = raise_escalation(db, st, body.course_id, clo, "manual", body.summary, created_by=user)
    db.commit()
    return esc_out(db, e)


@router.post("/escalations/{eid}/acknowledge")
def acknowledge(eid: int, db: Session = Depends(get_db), user: User = Depends(require("interventions:manage"))):
    e = db.get(Escalation, eid)
    if not e:
        raise HTTPException(404, "Escalation not found")
    ensure_course_access(db, user, e.course_id, write=True)
    if e.status != "open":
        raise HTTPException(409, f"Escalation is {e.status}")
    e.status, e.acknowledged_by, e.acknowledged_at = "acknowledged", user.id, utcnow()
    record(db, user, "escalation.acknowledged", "escalation", e.id, {}, course_id=e.course_id)
    db.commit()
    return esc_out(db, e)


def _resolve(db, user, e: Escalation, resolution: str):
    e.status, e.resolved_by, e.resolved_at, e.resolution = "resolved", user.id, utcnow(), resolution
    ai_gateway.close_review(db, "escalation", e.id, "resolved", user, resolution)
    record(db, user, "escalation.resolved", "escalation", e.id, {"resolution": resolution}, course_id=e.course_id)


@router.post("/escalations/{eid}/resolve")
def resolve(eid: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(require("interventions:manage"))):
    e = db.get(Escalation, eid)
    if not e:
        raise HTTPException(404, "Escalation not found")
    ensure_course_access(db, user, e.course_id, write=True)
    if e.status == "resolved":
        raise HTTPException(409, "Already resolved")
    if len(body.resolution.strip()) < MIN_REASON:
        raise HTTPException(422, f"Describe the resolution (at least {MIN_REASON} characters)")
    _resolve(db, user, e, body.resolution)
    db.commit()
    return esc_out(db, e)


@router.post("/interventions", status_code=201)
def intervene(body: InterventionIn, db: Session = Depends(get_db), user: User = Depends(require("interventions:manage"))):
    ensure_course_access(db, user, body.course_id, write=True)
    st = _student_in_course(db, body.student_id, body.course_id)
    if body.action not in ACTIONS:
        raise HTTPException(422, f"action must be one of {ACTIONS}")
    if len(body.reason.strip()) < MIN_REASON:
        raise HTTPException(422, f"A reason of at least {MIN_REASON} characters is required for every intervention")
    clo = db.get(CLO, body.clo_id) if body.clo_id else None
    if body.action in ("reset_attempts", "override_advance", "mastery_override") and (not clo or clo.course_id != body.course_id):
        raise HTTPException(422, "Choose the outcome (CLO) this intervention applies to")
    esc = db.get(Escalation, body.escalation_id) if body.escalation_id else None
    if esc and (esc.student_id != st.id or esc.course_id != body.course_id):
        raise HTTPException(422, "Escalation does not belong to this student and course")
    details: dict = {}

    if body.action == "reset_attempts":
        cyc = db.scalar(select(ReteachCycle).where(ReteachCycle.student_id == st.id, ReteachCycle.clo_id == clo.id)
                        .order_by(ReteachCycle.id.desc()).limit(1))
        if not cyc or cyc.status not in ("exhausted", "active"):
            raise HTTPException(409, "There is no reteach cycle to reset for this outcome")
        details = {"previous_status": cyc.status, "attempts_used": cyc.attempts_used}
        cyc.status = "reset"
        p = path_svc.active_path(db, st.id, body.course_id)
        if p:  # give the learner a fresh reteach step
            sts = [path_svc.step_dict(s) for s in path_svc.steps(db, p)]
            pos = next((i for i, s in enumerate(sts) if s["clo_id"] == clo.id and s["step_type"] == "reteach"), len(sts) - 1)
            sts.insert(pos + 1, dict(unit_id=clo.unit_id, clo_id=clo.id, step_type="reteach", status="pending", rule="I-RESET",
                                     rationale=f"Your instructor reopened reteaching for {clo.code}: {body.reason}", meta={"intervention": True}))
            path_svc.save_version(db, user, st.id, body.course_id, sts, p.summary, "override", reason=f"Instructor reset reteach attempts: {body.reason}")
    elif body.action == "override_advance":
        p = path_svc.active_path(db, st.id, body.course_id)
        if not p:
            raise HTTPException(409, "The learner has no learning path")
        sts = [path_svc.step_dict(s) for s in path_svc.steps(db, p)]
        n = 0
        for s in sts:
            if s["clo_id"] == clo.id and s["status"] == "pending":
                s["status"], s["completed_at"] = "skipped", utcnow()
                s["meta"] = {**s["meta"], "override": body.reason}
                n += 1
        path_svc.save_version(db, user, st.id, body.course_id, sts, p.summary, "override", reason=f"Instructor advanced past {clo.code}: {body.reason}")
        details = {"steps_skipped": n}
    elif body.action == "mastery_override":
        if body.attained is None:
            raise HTTPException(422, "State whether the outcome is to be recorded as attained")
        rec = mastery_svc.current(db, st.id, clo.id)
        if rec:
            rec.is_current = False
        from ..services import criteria as crit

        c = crit.resolve(db, clo)
        new = MasteryRecord(student_id=st.id, course_id=body.course_id, clo_id=clo.id,
                            mastery_pct=rec.mastery_pct if rec else 0.0, attained=body.attained, threshold=c["threshold"],
                            criterion_id=c["criterion_id"], criterion_version=c["version_no"], rule_version="instructor-override",
                            evidence_ids=rec.evidence_ids if rec else [], attempts_counted=rec.attempts_counted if rec else 0,
                            trigger="override", note=body.reason, calculated_by=user.id, is_current=True)
        db.add(new)
        db.flush()
        details = {"before": {"attained": rec.attained, "mastery_pct": rec.mastery_pct} if rec else None,
                   "after": {"attained": body.attained}, "mastery_record_id": new.id}

    iv = Intervention(escalation_id=esc.id if esc else None, student_id=st.id, course_id=body.course_id,
                      clo_id=clo.id if clo else None, instructor_id=user.id, action=body.action, reason=body.reason, details=details)
    db.add(iv)
    db.flush()
    record(db, user, f"intervention.{body.action}", "intervention", iv.id,
           {"student_id": st.id, "clo_id": iv.clo_id, "reason": body.reason, **details}, course_id=body.course_id)
    if esc and esc.status == "open":
        esc.status, esc.acknowledged_by, esc.acknowledged_at = "acknowledged", user.id, utcnow()
    if esc and body.resolve:
        _resolve(db, user, esc, f"{body.action}: {body.reason}")
    db.commit()
    return iv_out(db, iv)


@router.get("/interventions")
def list_interventions(course_id: int, student_id: int | None = None, db: Session = Depends(get_db),
                       user: User = Depends(require("interventions:manage", "reports:read"))):
    ensure_course_access(db, user, course_id)
    q = select(Intervention).where(Intervention.course_id == course_id).order_by(Intervention.id.desc())
    if student_id:
        q = q.where(Intervention.student_id == student_id)
    return [iv_out(db, i) for i in db.scalars(q)]
