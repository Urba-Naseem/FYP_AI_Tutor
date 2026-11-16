"""NB-4 - Review and resolve assessment appeals.

* A student appeals a completed quiz attempt (optionally one question) within APPEAL_DAYS.
* The appeal is routed to the instructor(s) currently teaching the course.
* The instructor resolves it: rejected (reason required) or upheld with a controlled
  grade amendment - the evidence is never edited in place: a new evidence version
  supersedes the old one, mastery is recalculated, and everything is audited.
* The student is notified in the app and by e-mail (outbox) of the outcome.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..core.rbac import accessible_course_ids, ensure_course_access, require
from ..db import get_db, utcnow
from ..models.appeals import Appeal, Notification
from ..models.mastery import MasteryEvidence
from ..models.outcomes import CLO, Course
from ..models.quiz import QuizAttempt
from ..models.user import User
from ..services import ai_gateway
from ..services import mastery as mastery_svc
from ..services.email import send_email

router = APIRouter(prefix="/api", tags=["appeals"])
APPEAL_DAYS = 14


class AppealIn(BaseModel):
    quiz_attempt_id: int
    item_id: int | None = None
    reason: str


class ResolveIn(BaseModel):
    decision: str  # upheld | rejected
    resolution: str
    new_correct: int | None = None  # required when upheld


def notify(db: Session, user: User, title: str, body: str, link: str | None = None):
    db.add(Notification(user_id=user.id, title=title, body=body, link=link))
    send_email(user.email, f"AI Tutor: {title}", f"Hello {user.full_name},\n\n{body}\n")


def appeal_out(db: Session, a: Appeal) -> dict:
    st = db.get(User, a.student_id)
    clo = db.get(CLO, a.clo_id)
    c = db.get(Course, a.course_id)
    rv = db.get(User, a.reviewer_id) if a.reviewer_id else None
    return {"id": a.id, "student": {"id": st.id, "name": st.full_name}, "course": {"id": c.id, "code": c.code},
            "clo_code": clo.code, "quiz_attempt_id": a.quiz_attempt_id, "item_id": a.item_id, "reason": a.reason,
            "status": a.status, "original_score": a.original_score, "amended_score": a.amended_score,
            "resolution": a.resolution, "reviewer": rv.full_name if rv else None, "created_at": a.created_at,
            "resolved_at": a.resolved_at}


@router.get("/appeals/eligible/{course_id}")
def eligible(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("appeals:submit"))):
    """Completed attempts the student may still appeal."""
    ensure_course_access(db, user, course_id)
    since = utcnow() - timedelta(days=APPEAL_DAYS)
    open_ids = {a.quiz_attempt_id for a in db.scalars(select(Appeal).where(Appeal.student_id == user.id, Appeal.status.in_(["submitted", "under_review"])))}
    out = []
    for a in db.scalars(select(QuizAttempt).where(QuizAttempt.student_id == user.id, QuizAttempt.course_id == course_id,
                                                  QuizAttempt.status == "completed", QuizAttempt.completed_at >= since)
                        .order_by(QuizAttempt.completed_at.desc())):
        ev = db.get(MasteryEvidence, a.evidence_id)
        out.append({"quiz_attempt_id": a.id, "clo_code": db.get(CLO, a.clo_id).code, "kind": a.kind, "completed_at": a.completed_at,
                    "score": f"{ev.correct}/{ev.total}" if ev else None, "item_ids": a.item_ids, "has_open_appeal": a.id in open_ids})
    return out


@router.post("/appeals", status_code=201)
def submit(body: AppealIn, db: Session = Depends(get_db), user: User = Depends(require("appeals:submit"))):
    a = db.get(QuizAttempt, body.quiz_attempt_id)
    if not a or a.student_id != user.id:
        raise HTTPException(404, "Attempt not found")
    if a.status != "completed" or not a.evidence_id:
        raise HTTPException(409, "Only completed attempts can be appealed")
    if a.completed_at < utcnow() - timedelta(days=APPEAL_DAYS):
        raise HTTPException(409, f"Appeals must be made within {APPEAL_DAYS} days")
    if body.item_id and body.item_id not in a.item_ids:
        raise HTTPException(422, "That question was not in this attempt")
    if len(body.reason.strip()) < 15:
        raise HTTPException(422, "Explain your appeal (at least 15 characters)")
    if db.scalar(select(Appeal).where(Appeal.quiz_attempt_id == a.id, Appeal.status.in_(["submitted", "under_review"]))):
        raise HTTPException(409, "There is already an open appeal for this attempt")
    # appeal the current version of the evidence for this attempt
    ev = db.scalar(select(MasteryEvidence).where(MasteryEvidence.source_ref == f"quiz_attempt:{a.id}", MasteryEvidence.status == "active"))
    if not ev:
        raise HTTPException(409, "This attempt's result is no longer active")
    ap = Appeal(student_id=user.id, course_id=a.course_id, clo_id=a.clo_id, quiz_attempt_id=a.id, evidence_id=ev.id,
                item_id=body.item_id, reason=body.reason.strip(), original_score=f"{ev.correct}/{ev.total}")
    db.add(ap)
    db.flush()
    ai_gateway.route_review(db, "appeal", "appeal", ap.id, a.course_id, f"Appeal by {user.full_name}: {body.reason[:120]}", user)
    record(db, user, "appeal.submitted", "appeal", ap.id, {"attempt": a.id, "item_id": body.item_id}, course_id=a.course_id)
    db.commit()
    return appeal_out(db, ap)


@router.get("/appeals/mine")
def mine(db: Session = Depends(get_db), user: User = Depends(require("appeals:submit"))):
    return [appeal_out(db, a) for a in db.scalars(select(Appeal).where(Appeal.student_id == user.id).order_by(Appeal.id.desc()))]


@router.get("/appeals")
def list_appeals(course_id: int | None = None, status: str | None = None, db: Session = Depends(get_db),
                 user: User = Depends(require("appeals:resolve", "audit:read"))):
    q = select(Appeal).order_by(Appeal.id.desc())
    if course_id:
        q = q.where(Appeal.course_id == course_id)
    if status:
        q = q.where(Appeal.status == status)
    allowed = accessible_course_ids(db, user)
    return [appeal_out(db, a) for a in db.scalars(q) if allowed is None or a.course_id in allowed]


@router.get("/appeals/{aid}")
def detail(aid: int, db: Session = Depends(get_db), user: User = Depends(require("appeals:resolve", "appeals:submit"))):
    ap = db.get(Appeal, aid)
    if not ap or (user.role == "student" and ap.student_id != user.id):
        raise HTTPException(404, "Appeal not found")
    ensure_course_access(db, user, ap.course_id)
    out = appeal_out(db, ap)
    if user.role != "student":  # reviewer sees the attempt with the key
        from .quiz import attempt_out

        att = db.get(QuizAttempt, ap.quiz_attempt_id)
        out["attempt"] = attempt_out(db, att)
    return out


@router.post("/appeals/{aid}/start-review")
def start_review(aid: int, db: Session = Depends(get_db), user: User = Depends(require("appeals:resolve"))):
    ap = db.get(Appeal, aid)
    if not ap:
        raise HTTPException(404, "Appeal not found")
    ensure_course_access(db, user, ap.course_id, write=True)
    if ap.status != "submitted":
        raise HTTPException(409, f"Appeal is {ap.status}")
    ap.status, ap.reviewer_id = "under_review", user.id
    record(db, user, "appeal.under_review", "appeal", ap.id, {}, course_id=ap.course_id)
    notify(db, db.get(User, ap.student_id), "Your appeal is being reviewed",
           f"Your appeal for {db.get(CLO, ap.clo_id).code} is now under review.", "/my-appeals")
    db.commit()
    return appeal_out(db, ap)


@router.post("/appeals/{aid}/resolve")
def resolve(aid: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(require("appeals:resolve"))):
    ap = db.get(Appeal, aid)
    if not ap:
        raise HTTPException(404, "Appeal not found")
    ensure_course_access(db, user, ap.course_id, write=True)
    if ap.status not in ("submitted", "under_review"):
        raise HTTPException(409, f"Appeal is already {ap.status}")
    if body.decision not in ("upheld", "rejected"):
        raise HTTPException(422, "decision must be upheld or rejected")
    if len(body.resolution.strip()) < 10:
        raise HTTPException(422, "Explain the decision to the student (at least 10 characters)")
    student = db.get(User, ap.student_id)
    clo = db.get(CLO, ap.clo_id)
    if body.decision == "upheld":
        ev = db.get(MasteryEvidence, ap.evidence_id)
        if body.new_correct is None or body.new_correct == ev.correct:
            raise HTTPException(422, "An upheld appeal needs the amended number of correct answers (different from the original)")
        if ev.status != "active":
            raise HTTPException(409, "The appealed result has already been amended")
        try:
            new, rec = mastery_svc.amend_evidence(db, ev.id, body.new_correct, f"Appeal #{ap.id} upheld: {body.resolution}", user)
        except ValueError as e:
            raise HTTPException(422, str(e))
        ap.amended_score, ap.amended_evidence_id = f"{new.correct}/{new.total}", new.id
        att = db.get(QuizAttempt, ap.quiz_attempt_id)
        att.score_pct = new.score_pct
        msg = (f"Your appeal for {clo.code} was upheld. Your score was changed from {ap.original_score} to {ap.amended_score}; "
               f"your mastery is now {rec.mastery_pct:.0f}% ({'attained' if rec.attained else 'not yet attained'}). {body.resolution}")
    else:
        msg = f"Your appeal for {clo.code} was not upheld. Your score of {ap.original_score} stands. {body.resolution}"
    ap.status, ap.resolution, ap.reviewer_id, ap.resolved_at = body.decision, body.resolution, user.id, utcnow()
    ai_gateway.close_review(db, "appeal", ap.id, "resolved", user, body.decision)
    record(db, user, f"appeal.{body.decision}", "appeal", ap.id,
           {"student_id": student.id, "original": ap.original_score, "amended": ap.amended_score, "resolution": body.resolution},
           course_id=ap.course_id)
    notify(db, student, f"Appeal {body.decision}", msg, "/my-appeals")
    db.commit()
    return appeal_out(db, ap)


# ---- notifications --------------------------------------------------------
@router.get("/notifications")
def notifications(db: Session = Depends(get_db), user: User = Depends(require("learn", "console:view", "users:manage", "audit:read"))):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(50))
    return [{"id": n.id, "title": n.title, "body": n.body, "link": n.link, "created_at": n.created_at, "read": n.read_at is not None} for n in rows]


@router.post("/notifications/{nid}/read")
def mark_read(nid: int, db: Session = Depends(get_db), user: User = Depends(require("learn", "console:view", "users:manage", "audit:read"))):
    n = db.get(Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Not found")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return {"ok": True}
