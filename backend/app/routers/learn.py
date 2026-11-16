"""US-5.1 - Deliver accessible unit material and learner error reporting.

Automation mode adds personalised lectures and notes per student and CLO (services/personal_lessons.py)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning, record
from ..core.rbac import ensure_course_access, require
from ..db import get_db, utcnow
from ..models.content import ContentDoc, ContentErrorReport, ContentVersion
from ..models.outcomes import CLO, Course, Unit
from ..models.paths import PathStep
from ..models.user import User
from ..services import ai_gateway, automation, personal_lessons
from .registration import require_consent

router = APIRouter(prefix="/api", tags=["learn"])


class ReportIn(BaseModel):
    category: str = "error"  # error | unclear | accessibility | other
    message: str
    excerpt: str | None = None


class ResolveIn(BaseModel):
    resolution: str


def approved_version(db: Session, doc_id: int) -> ContentVersion | None:
    return db.scalar(select(ContentVersion).where(ContentVersion.doc_id == doc_id, ContentVersion.status == "approved")
                     .order_by(ContentVersion.version_no.desc()).limit(1))


def _student_course(db, user, course_id):
    ensure_course_access(db, user, course_id)


@router.get("/learn/{course_id}/units")
def units(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    _student_course(db, user, course_id)
    out = []
    for u in db.scalars(select(Unit).where(Unit.course_id == course_id).order_by(Unit.number)):
        doc = db.scalar(select(ContentDoc).where(ContentDoc.unit_id == u.id, ContentDoc.kind == "lesson"))
        out.append({"id": u.id, "number": u.number, "title": u.title,
                    "available": automation.enabled() or bool(doc and approved_version(db, doc.id)),
                    "clos": [{"id": o.id, "code": o.code, "description": o.description}
                             for o in db.scalars(select(CLO).where(CLO.unit_id == u.id).order_by(CLO.code))]})
    return out


@router.get("/learn/{course_id}/units/{unit_id}")
def unit(course_id: int, unit_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn")),
         _c=Depends(require_consent)):
    _student_course(db, user, course_id)
    u = db.get(Unit, unit_id)
    if not u or u.course_id != course_id:
        raise HTTPException(404, "Unit not found")
    doc = db.scalar(select(ContentDoc).where(ContentDoc.unit_id == u.id, ContentDoc.kind == "lesson"))
    v = approved_version(db, doc.id) if doc else None
    alternates = []
    for alt in db.scalars(select(ContentDoc).where(ContentDoc.unit_id == u.id, ContentDoc.kind == "alternate")):
        av = approved_version(db, alt.id)
        if av:
            alternates.append({"clo_id": alt.clo_id, "title": alt.title, "version_id": av.id, "body_md": av.body_md})
    learning(db, user, "unit_viewed", "unit", u.id, {"version_id": v.id if v else None}, course_id=course_id)
    db.commit()
    return {"id": u.id, "number": u.number, "title": u.title,
            "clos": [{"id": o.id, "code": o.code, "description": o.description} for o in db.scalars(select(CLO).where(CLO.unit_id == u.id).order_by(CLO.code))],
            "lesson": {"version_id": v.id, "version_no": v.version_no, "title": doc.title, "body_md": v.body_md} if v else None,
            "alternates": alternates, "personal": automation.enabled()}


@router.get("/learn/{course_id}/clos/{clo_id}/lesson")
def personal_lesson(course_id: int, clo_id: int, refresh: bool = False, db: Session = Depends(get_db),
                    user: User = Depends(require("learn")), _c=Depends(require_consent)):
    """This learner's own lecture + notes for one CLO, pitched to their diagnostic / mastery level."""
    _student_course(db, user, course_id)
    if not automation.enabled():
        raise HTTPException(404, "Personalised lectures are switched off - use the course lesson")
    clo = db.get(CLO, clo_id)
    if not clo or clo.course_id != course_id:
        raise HTTPException(404, "Outcome not found")
    res = personal_lessons.get_or_create(db, user, db.get(Course, course_id), clo, refresh=refresh)
    db.commit()
    return res


@router.post("/learn/steps/{step_id}/complete")
def complete_step(step_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """Mark a study step (learn / remedial) done. Quiz steps are completed by the quiz itself."""
    s = db.get(PathStep, step_id)
    if not s:
        raise HTTPException(404, "Step not found")
    from ..models.paths import LearningPath

    p = db.get(LearningPath, s.path_id)
    if p.student_id != user.id or p.status != "active":
        raise HTTPException(404, "Step not found")
    if s.step_type not in ("learn", "remedial"):
        raise HTTPException(409, "This step is completed by passing its quiz")
    if s.status == "done":
        return {"ok": True}
    from ..services import paths as path_svc

    nxt = path_svc.next_step(db, p)
    if nxt is not None and nxt.id != s.id:
        label = (nxt.meta or {}).get("topic_title") or path_svc.STEP_LABEL.get(nxt.step_type, nxt.step_type)
        raise HTTPException(409, f"Finish your path in order first: your next step is \"{label}\". You can still read this material.")
    s.status, s.completed_at = "done", utcnow()
    learning(db, user, "step_completed", "path_step", s.id, {"type": s.step_type, "clo_id": s.clo_id}, course_id=p.course_id)
    db.commit()
    return {"ok": True}


@router.post("/learn/versions/{vid}/report", status_code=201)
def report(vid: int, body: ReportIn, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    v = db.get(ContentVersion, vid)
    if not v or v.status != "approved":
        raise HTTPException(404, "Material not found")
    doc = db.get(ContentDoc, v.doc_id)
    _student_course(db, user, doc.course_id)
    if body.category not in ("error", "unclear", "accessibility", "other"):
        raise HTTPException(422, "Unknown category")
    if len(body.message.strip()) < 5:
        raise HTTPException(422, "Please describe the problem")
    r = ContentErrorReport(version_id=v.id, course_id=doc.course_id, student_id=user.id, category=body.category,
                           message=body.message.strip(), excerpt=(body.excerpt or "")[:500] or None)
    db.add(r)
    db.flush()
    ai_gateway.route_review(db, "content_error", "content_error", r.id, doc.course_id,
                            f"{body.category.title()} reported in {doc.title} v{v.version_no}: {body.message[:120]}", user)
    learning(db, user, "content_reported", "content_version", v.id, {"report": r.id, "category": body.category}, course_id=doc.course_id)
    db.commit()
    return {"id": r.id, "status": r.status}


# ---- staff handling of reports ------------------------------------------------
@router.get("/content-reports")
def list_reports(course_id: int, status: str | None = "open", db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    ensure_course_access(db, user, course_id)
    q = select(ContentErrorReport).where(ContentErrorReport.course_id == course_id).order_by(ContentErrorReport.created_at.desc())
    if status:
        q = q.where(ContentErrorReport.status == status)
    out = []
    for r in db.scalars(q):
        v = db.get(ContentVersion, r.version_id)
        doc = db.get(ContentDoc, v.doc_id)
        out.append({"id": r.id, "doc_id": doc.id, "doc_title": doc.title, "version_no": v.version_no, "category": r.category,
                    "message": r.message, "excerpt": r.excerpt, "status": r.status, "resolution": r.resolution,
                    "created_at": r.created_at})
    return out


@router.post("/content-reports/{rid}/resolve")
def resolve_report(rid: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    r = db.get(ContentErrorReport, rid)
    if not r:
        raise HTTPException(404, "Report not found")
    ensure_course_access(db, user, r.course_id, write=True)
    if r.status != "open":
        raise HTTPException(409, "Already resolved")
    if len(body.resolution.strip()) < 3:
        raise HTTPException(422, "Describe the resolution")
    r.status, r.resolution, r.resolved_by, r.resolved_at = "resolved", body.resolution, user.id, utcnow()
    ai_gateway.close_review(db, "content_error", r.id, "resolved", user, body.resolution)
    record(db, user, "content.report_resolved", "content_error", r.id, {"resolution": body.resolution}, course_id=r.course_id)
    db.commit()
    return {"ok": True}



@router.get("/learn/{course_id}/instructors")
def my_instructors(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """Who teaches the student's own section of this course, and how to reach them (name, institutional e-mail,
    office hours, office, a short introduction). Only instructors currently assigned to that section."""
    from ..models.enrollment import Enrollment
    from ..models.offerings import InstructorAssignment, Section, Term
    from .offerings import active_assignments

    _student_course(db, user, course_id)
    e = db.scalar(select(Enrollment).where(Enrollment.student_id == user.id, Enrollment.course_id == course_id,
                                           Enrollment.status.in_(("enrolled", "completed"))).order_by(Enrollment.id.desc()).limit(1))
    if not e:
        return {"section": None, "instructors": []}
    sec = db.get(Section, e.section_id)
    term = db.get(Term, sec.term_id) if sec else None
    rows = db.scalars(active_assignments(db).where(InstructorAssignment.section_id == e.section_id)
                      .order_by(InstructorAssignment.role.desc(), InstructorAssignment.start_date)).all()
    out = []
    for a in rows:
        t = db.get(User, a.instructor_id)
        if t and t.is_active:
            out.append({"name": t.full_name, "email": t.email, "role": a.role, "since": a.start_date,
                        "office_hours": t.office_hours, "office_location": t.office_location, "about": t.about})
    return {"section": {"code": sec.code, "term": term.name if term else None} if sec else None, "instructors": out}
