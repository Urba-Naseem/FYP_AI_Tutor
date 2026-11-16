"""US-13.1 - Instructor console: attainment, pending reviews and intervention access."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.rbac import accessible_course_ids, ensure_course_access, require
from ..db import get_db, utcnow
from ..models.ai import ReviewTask
from ..models.audit import AuditEvent
from ..models.diagnostic import DiagnosticAttempt
from ..models.enrollment import Enrollment
from ..models.mastery import MasteryRecord
from ..models.outcomes import CLO, Course
from ..models.reteach import ReteachCycle
from ..models.user import User
from ..services import criteria as crit
from ..services import paths as path_svc


def _program_code(db, course):
    from ..services import departments

    return departments.course_program(db, course)

router = APIRouter(prefix="/api/console", tags=["console"])
INACTIVE_DAYS = 14


def _enrolled(db: Session, course_id: int) -> list[User]:
    ids = [e.student_id for e in db.scalars(select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.status == "enrolled"))]
    return list(db.scalars(select(User).where(User.id.in_(ids)).order_by(User.id))) if ids else []


def _current_records(db: Session, course_id: int) -> list[MasteryRecord]:
    return db.scalars(select(MasteryRecord).where(MasteryRecord.course_id == course_id, MasteryRecord.is_current.is_(True))).all()


def flags_for(db: Session, course_id: int, student: User, recs: list[MasteryRecord]) -> list[dict]:
    """Why a learner may need an intervention."""
    out = []
    mine = [r for r in recs if r.student_id == student.id]
    clos = {o.id: o.code for o in db.scalars(select(CLO).where(CLO.course_id == course_id))}
    for r in mine:
        if not r.attained and r.attempts_counted >= 2:
            out.append({"type": "below_after_attempts", "clo_code": clos.get(r.clo_id),
                        "message": f"{clos.get(r.clo_id)} at {r.mastery_pct:.0f}% after {r.attempts_counted} attempts"})
    for c in db.scalars(select(ReteachCycle).where(ReteachCycle.student_id == student.id, ReteachCycle.course_id == course_id,
                                                   ReteachCycle.status == "exhausted")):
        out.append({"type": "reteach_exhausted", "clo_code": clos.get(c.clo_id), "message": f"Reteach attempts used up on {clos.get(c.clo_id)}"})
    try:  # US-6.4 escalations
        from ..models.escalation import Escalation

        for e in db.scalars(select(Escalation).where(Escalation.student_id == student.id, Escalation.course_id == course_id,
                                                     Escalation.status.in_(["open", "acknowledged"]))):
            out.append({"type": "escalation", "escalation_id": e.id, "clo_code": clos.get(e.clo_id), "message": e.summary})
    except ImportError:
        pass
    last = db.scalar(select(func.max(AuditEvent.at)).where(AuditEvent.actor_id == student.id, AuditEvent.course_id == course_id,
                                                           AuditEvent.category == "learning"))
    if last is None or last < utcnow() - timedelta(days=INACTIVE_DAYS):
        out.append({"type": "inactive", "clo_code": None,
                    "message": "No learning activity yet" if last is None else f"Inactive for over {INACTIVE_DAYS} days"})
    return out


def course_summary(db: Session, c: Course) -> dict:
    students = _enrolled(db, c.id)
    recs = [r for r in _current_records(db, c.id) if r.student_id in {s.id for s in students}]
    clos = db.scalars(select(CLO).where(CLO.course_id == c.id).order_by(CLO.code)).all()
    attainment = []
    for o in clos:
        rs = [r for r in recs if r.clo_id == o.id]
        attainment.append({"clo_id": o.id, "clo_code": o.code, "description": o.description,
                           "threshold": crit.resolve(db, o)["threshold"], "assessed": len(rs),
                           "attained": sum(1 for r in rs if r.attained),
                           "attained_pct": round(100.0 * sum(1 for r in rs if r.attained) / len(rs), 1) if rs else None,
                           "avg_mastery": round(sum(r.mastery_pct for r in rs) / len(rs), 1) if rs else None})
    pending = db.execute(select(ReviewTask.kind, func.count()).where(ReviewTask.course_id == c.id, ReviewTask.status == "pending")
                         .group_by(ReviewTask.kind)).all()
    diag_done = db.scalar(select(func.count()).select_from(DiagnosticAttempt).where(
        DiagnosticAttempt.course_id == c.id, DiagnosticAttempt.status == "completed"))
    needing = []
    for s in students:
        f = flags_for(db, c.id, s, recs)
        serious = [x for x in f if x["type"] != "inactive"]
        if f:
            needing.append({"student_id": s.id, "name": s.full_name, "student_number": s.student_number,
                            "flags": f, "priority": "high" if serious else "low"})
    needing.sort(key=lambda x: (x["priority"] != "high", -len(x["flags"])))
    return {"course": {"id": c.id, "code": c.code, "title": c.title, "status": c.status, "program": _program_code(db, c)},
            "enrolled": len(students), "diagnostic_completed": diag_done, "attainment": attainment,
            "pending_reviews": {k: n for k, n in pending}, "pending_total": sum(n for _, n in pending),
            "needs_intervention": needing}


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(require("console:view"))):
    allowed = accessible_course_ids(db, user)
    q = select(Course).where(Course.status.in_(["approved", "active"])).order_by(Course.code)
    out = []
    for c in db.scalars(q):
        if allowed is not None and c.id not in allowed:
            continue
        s = course_summary(db, c)
        out.append({**s, "needs_intervention": len([x for x in s["needs_intervention"] if x["priority"] == "high"]),
                    "inactive": len([x for x in s["needs_intervention"] if x["priority"] == "low"])})
    return out


@router.get("/course/{course_id}")
def course_detail(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("console:view"))):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "Course not found")
    ensure_course_access(db, user, course_id)
    s = course_summary(db, c)
    recs = _current_records(db, course_id)
    clos = [a["clo_code"] for a in s["attainment"]]
    clo_ids = {a["clo_code"]: a["clo_id"] for a in s["attainment"]}
    matrix = []
    for st in _enrolled(db, course_id):
        row = {"student_id": st.id, "name": st.full_name, "student_number": st.student_number, "clos": {}}
        for code in clos:
            r = next((x for x in recs if x.student_id == st.id and x.clo_id == clo_ids[code]), None)
            row["clos"][code] = {"mastery_pct": r.mastery_pct, "attained": r.attained} if r else None
        p = path_svc.active_path(db, st.id, course_id)
        if p:
            sts = path_svc.steps(db, p)
            row["path"] = {"version": p.version_no, "locked": p.locked,
                           "done": sum(1 for x in sts if x.status in ("done", "skipped")), "total": len(sts)}
        else:
            row["path"] = None
        matrix.append(row)
    return {**s, "clo_codes": clos, "matrix": matrix}


@router.get("/course/{course_id}/student/{student_id}")
def student_detail(course_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(require("console:view"))):
    ensure_course_access(db, user, course_id)
    st = db.get(User, student_id)
    if not st or not db.scalar(select(Enrollment).where(Enrollment.student_id == student_id, Enrollment.course_id == course_id)):
        raise HTTPException(404, "Student not enrolled in this course")
    from .diagnostic import results_out
    from .mastery import student_course_mastery
    from .paths import path_out

    diag = db.scalar(select(DiagnosticAttempt).where(DiagnosticAttempt.student_id == student_id, DiagnosticAttempt.course_id == course_id,
                                                     DiagnosticAttempt.status == "completed").order_by(DiagnosticAttempt.id.desc()).limit(1))
    p = path_svc.active_path(db, student_id, course_id)
    events = db.scalars(select(AuditEvent).where(AuditEvent.actor_id == student_id, AuditEvent.course_id == course_id,
                                                 AuditEvent.category == "learning").order_by(AuditEvent.id.desc()).limit(25)).all()
    return {"student": {"id": st.id, "name": st.full_name, "student_number": st.student_number, "email": st.email},
            "flags": flags_for(db, course_id, st, _current_records(db, course_id)),
            "mastery": student_course_mastery(db, student_id, course_id)["clos"],
            "diagnostic": results_out(db, diag) if diag else None,
            "path": path_out(db, p) if p else None,
            "recent_activity": [{"at": e.at, "action": e.action, "details": e.details} for e in events]}
