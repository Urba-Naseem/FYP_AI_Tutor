"""US-4.1 - Deliver the diagnostic (adaptive selection, autosave/resume, per-CLO readiness)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning, record
from ..core.rbac import ensure_course_access, require
from ..db import get_db, utcnow
from ..models.diagnostic import DiagnosticAttempt, DiagnosticResponse, ReadinessResult
from ..models.items import DiagnosticConfig, Item
from ..models.outcomes import CLO, Course
from ..models.user import User
from ..services import automation
from ..services import diagnostic as svc
from ..services import shuffle
from .registration import require_consent

router = APIRouter(prefix="/api/diagnostic", tags=["diagnostic"])


class DraftIn(BaseModel):
    item_id: int
    choice: int | None


class AnswerIn(BaseModel):
    item_id: int
    choice: int


class ResetIn(BaseModel):
    reason: str


def _attempt(db: Session, aid: int, user: User) -> DiagnosticAttempt:
    a = db.get(DiagnosticAttempt, aid)
    if not a or a.student_id != user.id:
        raise HTTPException(404, "Attempt not found")
    return a


def question_out(it: Item, attempt_id: int | None = None, position: int = 0) -> dict:
    """Student view of an item: never includes the answer, explanation or hints. Options come in this student's
    own order (same difficulty and outcome for everyone, but questions and option order differ per student)."""
    opts = it.options if attempt_id is None else shuffle.shown(it.options, shuffle.order(shuffle.diagnostic_key(attempt_id), position, len(it.options), it.answer))
    return {"id": it.id, "stem": it.stem, "options": opts, "clo_id": it.clo_id}


def state(db: Session, a: DiagnosticAttempt) -> dict:
    answered = len(db.scalars(select(DiagnosticResponse.id).where(DiagnosticResponse.attempt_id == a.id)).all())
    d = {"attempt_id": a.id, "status": a.status, "answered": answered, "total": svc.total_planned(db, a),
         "started_at": a.started_at, "last_saved_at": a.last_saved_at, "completed_at": a.completed_at}
    if a.status == "in_progress":
        it = db.get(Item, a.current_item_id) if a.current_item_id else None
        d["question"] = question_out(it, a.id, answered) if it else None  # position = questions answered so far
        d["draft_choice"] = a.draft_choice
    return d


def results_out(db: Session, a: DiagnosticAttempt) -> list[dict]:
    rows = db.scalars(select(ReadinessResult).where(ReadinessResult.attempt_id == a.id)).all()
    clos = {o.id: o for o in db.scalars(select(CLO).where(CLO.course_id == a.course_id))}
    return sorted([{"clo_id": r.clo_id, "clo_code": clos[r.clo_id].code, "description": clos[r.clo_id].description,
                    "score_pct": r.score_pct, "level": r.level, "answered": r.answered, "correct": r.correct} for r in rows],
                  key=lambda x: x["clo_code"])


@router.get("/{course_id}/status")
def status(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    ensure_course_access(db, user, course_id)
    cfg = db.get(DiagnosticConfig, course_id)
    atts = db.scalars(select(DiagnosticAttempt).where(DiagnosticAttempt.student_id == user.id, DiagnosticAttempt.course_id == course_id,
                                                      DiagnosticAttempt.status != "void").order_by(DiagnosticAttempt.id.desc())).all()
    current = atts[0] if atts else None
    published = bool(cfg and cfg.published)
    preparing = False
    course = db.get(Course, course_id)
    mode = svc.diagnostic_mode(db, user.id, course) if course else {"mode": "test", "reason": "", "prerequisites": [], "score_pct": None}
    if mode["mode"] == "prerequisite" and not current:  # no test for this student: the path uses the prerequisite result
        return {"published": published, "preparing": False, "attempt": None, "results": None, "needed": False, **mode}
    if not published and automation.enabled():
        # Automation: the diagnostic is written from the CLOs on demand if it is not ready yet.
        if course and course.status in ("approved", "active"):
            automation.prepare_course_async(course_id, None)
            db.expire_all()
            cfg = db.get(DiagnosticConfig, course_id)
            published = bool(cfg and cfg.published)
            preparing = not published and automation.is_preparing(course_id)
    return {"published": published, "preparing": preparing, "attempt": state(db, current) if current else None,
            "results": results_out(db, current) if current and current.status == "completed" else None,
            "needed": True, **mode}


@router.post("/{course_id}/start")
def start(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn")), _c=Depends(require_consent)):
    ensure_course_access(db, user, course_id)
    course = db.get(Course, course_id)
    if course and svc.diagnostic_mode(db, user.id, course)["mode"] == "prerequisite":
        raise HTTPException(409, "No diagnostic is needed for this course: your learning path is built from your "
                                 "prerequisite result")
    cfg = db.get(DiagnosticConfig, course_id)
    if not cfg or not cfg.published:
        raise HTTPException(409, "The diagnostic for this course is not available yet")
    existing = db.scalar(select(DiagnosticAttempt).where(DiagnosticAttempt.student_id == user.id, DiagnosticAttempt.course_id == course_id,
                                                         DiagnosticAttempt.status.in_(["in_progress", "completed"])))
    if existing:
        if existing.status == "completed":
            raise HTTPException(409, "You have already completed this diagnostic")
        return state(db, existing)  # resume
    a = DiagnosticAttempt(student_id=user.id, course_id=course_id, items_per_clo=cfg.items_per_clo)
    db.add(a)
    db.flush()
    first = svc.next_item(db, a)
    a.current_item_id = first.id if first else None
    learning(db, user, "diagnostic_started", "diagnostic_attempt", a.id, {}, course_id=course_id)
    db.commit()
    return state(db, a)


@router.get("/attempts/{aid}")
def current(aid: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    return state(db, _attempt(db, aid, user))


@router.put("/attempts/{aid}/draft")
def autosave(aid: int, body: DraftIn, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """Autosave the selected (not yet submitted) option so a refresh or lost connection loses nothing."""
    a = _attempt(db, aid, user)
    if a.status != "in_progress" or a.current_item_id != body.item_id:
        raise HTTPException(409, "This question is no longer current")
    a.draft_choice, a.last_saved_at = body.choice, utcnow()
    db.commit()
    return {"saved_at": a.last_saved_at}


@router.post("/attempts/{aid}/answer")
def answer(aid: int, body: AnswerIn, db: Session = Depends(get_db), user: User = Depends(require("learn")), _c=Depends(require_consent)):
    a = _attempt(db, aid, user)
    if a.status != "in_progress":
        raise HTTPException(409, "This attempt is finished")
    if a.current_item_id != body.item_id:
        raise HTTPException(409, "Answer the current question")
    it = db.get(Item, body.item_id)
    if not 0 <= body.choice < len(it.options):
        raise HTTPException(422, "Invalid option")
    seq = len(db.scalars(select(DiagnosticResponse.id).where(DiagnosticResponse.attempt_id == a.id)).all()) + 1
    chosen = shuffle.to_original(shuffle.order(shuffle.diagnostic_key(a.id), seq - 1, len(it.options), it.answer), body.choice)
    correct = chosen == it.answer
    db.add(DiagnosticResponse(attempt_id=a.id, item_id=it.id, item_version=it.version_no, clo_id=it.clo_id,
                              difficulty=it.difficulty, chosen=chosen, correct=correct, seq=seq))
    db.flush()
    learning(db, user, "diagnostic_answer", "item", it.id, {"attempt": a.id, "correct": correct, "difficulty": it.difficulty},
             course_id=a.course_id)
    nxt = svc.next_item(db, a)
    a.draft_choice, a.last_saved_at = None, utcnow()
    if nxt:
        a.current_item_id = nxt.id
    else:
        a.current_item_id, a.status, a.completed_at = None, "completed", utcnow()
        res = svc.score(db, a)
        learning(db, user, "diagnostic_completed", "diagnostic_attempt", a.id,
                 {r.clo_id: r.level for r in res}, course_id=a.course_id)
        try:  # US-4.3: build the learning path from the new baseline
            from ..services.paths import generate_path

            generate_path(db, user, a.course_id, trigger="diagnostic")
        except ImportError:
            pass
    db.commit()
    out = state(db, a)
    if a.status == "completed":
        out["results"] = results_out(db, a)
    return out


@router.get("/attempts/{aid}/results")
def results(aid: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    a = _attempt(db, aid, user)
    if a.status != "completed":
        raise HTTPException(409, "Finish the diagnostic first")
    return results_out(db, a)


# ---- staff ---------------------------------------------------------------
@router.get("/{course_id}/results")
def course_results(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("reports:read"))):
    ensure_course_access(db, user, course_id)
    out = []
    for a in db.scalars(select(DiagnosticAttempt).where(DiagnosticAttempt.course_id == course_id).order_by(DiagnosticAttempt.id)):
        st = db.get(User, a.student_id)
        out.append({"attempt_id": a.id, "student": {"id": st.id, "name": st.full_name}, "status": a.status,
                    "completed_at": a.completed_at, "results": results_out(db, a) if a.status == "completed" else []})
    return out


@router.post("/attempts/{aid}/reset")
def reset(aid: int, body: ResetIn, db: Session = Depends(get_db), user: User = Depends(require("interventions:manage"))):
    a = db.get(DiagnosticAttempt, aid)
    if not a:
        raise HTTPException(404, "Attempt not found")
    ensure_course_access(db, user, a.course_id, write=True)
    if len(body.reason.strip()) < 5:
        raise HTTPException(422, "A reason is required")
    a.status, a.void_reason = "void", body.reason
    record(db, user, "diagnostic.reset", "diagnostic_attempt", a.id, {"reason": body.reason, "student_id": a.student_id}, course_id=a.course_id)
    db.commit()
    return {"ok": True}


@router.get("/courses/mine")
def my_courses(db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    from ..models.enrollment import Enrollment

    rows = db.scalars(select(Enrollment).where(Enrollment.student_id == user.id, Enrollment.status == "enrolled")).all()
    return [{"id": c.id, "code": c.code, "title": c.title} for c in (db.get(Course, e.course_id) for e in rows)]
