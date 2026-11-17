"""US-13.2 - AI status, grounded tutor answers, call tracing and the review queue."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.audit import record
from ..core.rbac import accessible_course_ids, ensure_course_access, require
from ..db import get_db, utcnow
from ..models.ai import AICall, ReviewTask
from ..models.outcomes import Course
from ..models.user import User
from ..services import ai_gateway, llm

router = APIRouter(prefix="/api", tags=["ai"])


class PastTurn(BaseModel):
    question: str
    answer: str


class AskIn(BaseModel):
    course_id: int
    question: str
    history: list[PastTurn] = []  # earlier turns of this conversation (for Socratic follow-ups)


SOCRATIC_NUDGE = (
    "TEACHING STYLE - SOCRATIC NUDGING (the learner is a student studying this course):\n"
    "- For a NEW conceptual question where the learner has not shown their own thinking yet, do NOT give the full "
    "answer. Reply briefly: acknowledge the question, give a nudge (a related idea they already know, or where in the "
    "outcome / reading to look) and end with ONE guiding question that moves them one step closer.\n"
    "- When the learner replies to your guiding question, build on what they said: confirm what is right, correct "
    "gently, and nudge further.\n"
    "- Give a complete, clear explanation only when they have reasoned it through, after two real attempts, or when "
    "they say they are stuck or ask you to explain it; then end with one quick check question.\n"
    "- Simple factual look-ups (a definition, where something is covered) may be answered directly.\n"
    "Keep replies under about 120 words.\n"
)


class FlagIn(BaseModel):
    reason: str


class ResolveIn(BaseModel):
    note: str


@router.get("/ai/status")
def status(_=Depends(require("ai:use", "ai:logs", "reports:read"))):
    s = get_settings()
    return {"enabled": llm.ai_enabled(), "mode": s.ai_mode, "model": s.gemini_model,
            "provider": "Google Gemini", "key_configured": bool(s.gemini_api_key.strip()), "automation": bool(s.automation)}


@router.post("/ai/ask")
def ask(body: AskIn, db: Session = Depends(get_db), user: User = Depends(require("ai:use"))):
    """Grounded tutor answer from the course's approved sources, with citations."""
    if not db.get(Course, body.course_id):
        raise HTTPException(404, "Course not found")
    ensure_course_access(db, user, body.course_id)
    if len(body.question.strip()) < 3:
        raise HTTPException(422, "Please type a question")
    protected = []
    try:  # never let the tutor reveal keys of items the learner may be assessed on
        from ..services.items import protected_answers

        protected = protected_answers(db, body.course_id)
    except ImportError:
        pass
    extra = ""
    if user.role == "student":
        past = "\n".join(f"LEARNER: {ai_gateway.fence(t.question[:700])}\nTUTOR: {t.answer[:700]}" for t in body.history[-4:])
        extra = SOCRATIC_NUDGE + (f"\nCONVERSATION SO FAR:\n{past}\n" if past else "")
    res = ai_gateway.grounded_answer(db, user, body.course_id, body.question, protected=protected, extra_instructions=extra)
    db.commit()
    return res


@router.post("/ai/calls/{cid}/flag")
def flag_answer(cid: int, body: FlagIn, db: Session = Depends(get_db), user: User = Depends(require("ai:use"))):
    """A learner (or staff member) reports a problematic AI answer -> instructor review."""
    call = db.get(AICall, cid)
    if not call or (call.user_id != user.id and user.role not in ("admin", "instructor")):
        raise HTTPException(404, "AI answer not found")
    if not call.course_id:
        raise HTTPException(422, "This AI output is not linked to a course")
    t = ai_gateway.route_review(db, "tutor_answer", "ai_call", call.id, call.course_id,
                                f"Flagged AI answer: {body.reason[:200]}", created_by=user, ai_call_id=call.id)
    db.commit()
    return {"review_task_id": t.id}


def call_out(c: AICall, full: bool = False) -> dict:
    d = {"id": c.id, "at": c.at, "user_id": c.user_id, "user_role": c.user_role, "purpose": c.purpose, "course_id": c.course_id,
         "model": c.model, "status": c.status, "prompt_sha256": c.prompt_sha256[:16], "sources": len(c.source_chunk_ids),
         "guard_flags": c.guard_flags, "error": c.error, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
         "latency_ms": c.latency_ms, "request_id": c.request_id}
    if full:
        d.update(prompt=c.prompt, response=c.response, source_chunk_ids=c.source_chunk_ids)
    return d


@router.get("/ai/calls")
def list_calls(purpose: str | None = None, status: str | None = None, course_id: int | None = None,
               limit: int = Query(100, le=500), db: Session = Depends(get_db), _=Depends(require("ai:logs"))):
    q = select(AICall)
    if purpose:
        q = q.where(AICall.purpose.like(f"{purpose}%"))
    if status:
        q = q.where(AICall.status == status)
    if course_id:
        q = q.where(AICall.course_id == course_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    stats = dict(db.execute(select(AICall.status, func.count()).group_by(AICall.status)).all())
    return {"total": total, "stats": stats, "items": [call_out(c) for c in db.scalars(q.order_by(AICall.id.desc()).limit(limit))]}


@router.get("/ai/calls/{cid}")
def get_call(cid: int, db: Session = Depends(get_db), user: User = Depends(require("ai:logs"))):
    c = db.get(AICall, cid)
    if not c:
        raise HTTPException(404, "Not found")
    record(db, user, "ai.trace_viewed", "ai_call", c.id, {})
    db.commit()
    return call_out(c, full=True)


# ---- review queue (basic instructor review interface) ---------------------------
def task_out(db: Session, t: ReviewTask) -> dict:
    c = db.get(Course, t.course_id)
    d = {"id": t.id, "kind": t.kind, "ref_type": t.ref_type, "ref_id": t.ref_id, "course_id": t.course_id,
         "course_code": c.code if c else None, "title": t.title, "status": t.status, "created_at": t.created_at,
         "decided_at": t.decided_at, "note": t.note, "ai_call_id": t.ai_call_id}
    if t.ref_type == "ai_call":
        call = db.get(AICall, t.ref_id)
        if call:  # show the reviewer what the learner saw
            question = call.prompt.split("<student_input>")[-1].split("</student_input>")[0].strip()
            d["detail"] = {"question": question, "response": call.response, "status": call.status}
    return d


@router.get("/reviews")
def reviews(status: str | None = "pending", kind: str | None = None, course_id: int | None = None,
            db: Session = Depends(get_db), user: User = Depends(require("console:view"))):
    """Pending reviews for the courses the user currently teaches (admins: all)."""
    q = select(ReviewTask).order_by(ReviewTask.created_at.desc())
    if status:
        q = q.where(ReviewTask.status == status)
    if kind:
        q = q.where(ReviewTask.kind == kind)
    if course_id:
        q = q.where(ReviewTask.course_id == course_id)
    allowed = accessible_course_ids(db, user)
    return [task_out(db, t) for t in db.scalars(q) if allowed is None or t.course_id in allowed]


@router.post("/reviews/{tid}/resolve")
def resolve(tid: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(require("console:view"))):
    t = db.get(ReviewTask, tid)
    if not t:
        raise HTTPException(404, "Review not found")
    ensure_course_access(db, user, t.course_id, write=True)
    if t.kind != "tutor_answer":
        raise HTTPException(409, "Decide this review from its own screen (approve / reject the item)")
    if t.status != "pending":
        raise HTTPException(409, "Already decided")
    t.status, t.decided_by, t.decided_at, t.note = "resolved", user.id, utcnow(), body.note
    record(db, user, "review.resolved", "review_task", t.id, {"note": body.note}, course_id=t.course_id)
    db.commit()
    return task_out(db, t)
