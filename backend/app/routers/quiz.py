"""US-6.1 - Deliver formative quizzes and update CLO mastery.

* A formative quiz has QUIZ_SIZE (10) questions, ALL from the teacher-approved bank and ALL counted in the score:
  3-5 written questions (true/false, fill in the blank, short answer - services/quiz_written.py) and multiple choice
  for the rest. The AI only drafts questions; a teacher approves every one before a student can see it. When the bank
  has too few written questions the quiz uses more multiple choice (and the other way round).
* Answers are saved as the student goes and may be changed until the quiz is submitted. Nothing is marked or revealed
  before submission; the full result (score, every answer, the right answers and why) is shown at the end, followed by
  the teacher debrief about the wrong answers (routers/debrief.py).
* Formative quizzes have no hints (hints stay in reteach quizzes and practice).
"""
import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.audit import learning
from ..core.rbac import ensure_course_access, require
from ..db import get_db, utcnow
from ..models.items import Item
from ..models.mastery import MasteryRecord
from ..models.outcomes import CLO
from ..models.paths import LearningPath, PathStep
from ..models.quiz import QuizAnswer, QuizAttempt
from ..models.user import User
from ..services import mastery
from ..services import shuffle
from .registration import require_consent

router = APIRouter(prefix="/api/quiz", tags=["quiz"])
QUIZ_SIZE = 10            # formative quiz length (every question counts)
RETEACH_SIZE = 5          # reteach quizzes stay short
WRITTEN_RANGE = (3, 5)    # written questions per formative quiz, when the bank has them
MIN_ITEMS = 3


class StartIn(BaseModel):
    course_id: int
    clo_id: int
    step_id: int | None = None
    kind: str = "formative"


class AnswerIn(BaseModel):
    item_id: int
    choice: int | None = None  # multiple choice: the option index as shown
    text: str | None = None    # written questions: the student's answer (true_false: "true" / "false")


def _is_written(it: Item) -> bool:
    return it.kind != "mcq"


def _seen_counts(db: Session, student_id: int, clo_id: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    for a in db.scalars(select(QuizAttempt).where(QuizAttempt.student_id == student_id, QuizAttempt.clo_id == clo_id)):
        for i in a.item_ids:
            counts[i] = counts.get(i, 0) + 1
    return counts


def target_difficulty(db: Session, student_id: int, course_id: int | None, clo_id: int, kind: str = "formative") -> int:
    """The difficulty that fits THIS student on THIS outcome (1 easy - 3 hard): from their current mastery, or - before
    any practice - their own diagnostic score. A reteach quiz aims one step easier."""
    from ..services import mastery as mastery_svc
    from ..services.diagnostic import latest_readiness

    rec = mastery_svc.current(db, student_id, clo_id)
    pct = rec.mastery_pct if rec else None
    if pct is None and course_id is not None:
        r = latest_readiness(db, student_id, course_id).get(clo_id)
        pct = r.score_pct if r else None
    level = 2 if pct is None else 1 if pct < 40 else 2 if pct < 70 else 3
    return max(1, level - 1) if kind == "reteach" else level


def pick_items(db: Session, student_id: int, clo_id: int, purposes: tuple[str, ...], size: int = QUIZ_SIZE,
               unseen_only: bool = False, course_id: int | None = None, kind: str = "formative",
               written: bool | None = None) -> list[Item]:
    """A personal question set from the teacher-approved bank:
      1. never a question this student has already seen while unseen ones remain,
      2. difficulty matched to the student's own level (diagnostic, then mastery),
      3. a per-student shuffle among equally good questions, so two students rarely get the same set.
    When the student is running out of unseen questions, more are drafted for the teacher to approve."""
    import random

    pool = db.scalars(select(Item).where(Item.clo_id == clo_id, Item.purpose.in_(purposes), Item.status == "approved")).all()
    if written is not None:  # only written questions, or only multiple choice
        pool = [i for i in pool if _is_written(i) == written]
    seen = _seen_counts(db, student_id, clo_id)
    unseen = [i for i in pool if i.id not in seen]
    if unseen_only:
        pool = unseen
    target = target_difficulty(db, student_id, course_id, clo_id, kind)
    attempts = db.scalar(select(func.count(QuizAttempt.id)).where(QuizAttempt.student_id == student_id, QuizAttempt.clo_id == clo_id)) or 0
    rnd = random.Random(f"{student_id}:{clo_id}:{attempts}")
    shuffle = {i.id: rnd.random() for i in pool}
    # least-seen questions first (unseen before seen); within that, about 60% at the student's own level and the rest
    # from the neighbouring level, each chosen by the per-student shuffle
    fresh = min(seen.get(i.id, 0) for i in pool) if pool else 0
    first = [i for i in pool if seen.get(i.id, 0) == fresh]
    rest = sorted([i for i in pool if seen.get(i.id, 0) != fresh], key=lambda i: (seen.get(i.id, 0), abs(i.difficulty - target), shuffle[i.id]))
    by_dist = {d: sorted([i for i in first if abs(i.difficulty - target) == d], key=lambda i: shuffle[i.id]) for d in (0, 1, 2)}
    own = by_dist[0][:max(1, round(size * 0.6))]
    near = [i for d in (1, 2) for i in by_dist[d]]
    picked = own + near[:size - len(own)]
    picked += [i for i in by_dist[0] if i not in picked][:size - len(picked)]
    picked += rest[:size - len(picked)]
    picked = picked[:size]
    if course_id is not None and len(unseen) < 2 * size:
        from ..services import automation

        automation.request_more_items(course_id, clo_id, "reteach" if kind == "reteach" else "formative")
    return picked


def _attempt(db: Session, aid: int, user: User) -> QuizAttempt:
    a = db.get(QuizAttempt, aid)
    if not a or a.student_id != user.id:
        raise HTTPException(404, "Quiz not found")
    return a


def _tf(chosen: int | None) -> str:
    return "" if chosen is None or chosen < 0 else ("True" if chosen == 0 else "False")


def attempt_out(db: Session, a: QuizAttempt) -> dict:
    answers = {x.item_id: x for x in db.scalars(select(QuizAnswer).where(QuizAnswer.attempt_id == a.id))}
    done = a.status == "completed"
    qs = []
    for pos, iid in enumerate(a.item_ids):
        it = db.get(Item, iid)
        t = it.kind
        ans = answers.get(iid)
        q = {"id": it.id, "type": t, "stem": it.stem, "hints_available": a.kind == "reteach"}
        if t == "mcq":
            p = shuffle.order(shuffle.quiz_key(a.id), pos, len(it.options), it.answer)  # this attempt's balanced option order
            q["options"] = shuffle.shown(it.options, p)
            if ans:
                q["response"] = {"choice": shuffle.to_shown(p, ans.chosen)}  # the saved choice (not marked yet)
                if done:  # marks, right answers and explanations only once the quiz is submitted
                    q["answer"] = {"chosen": shuffle.to_shown(p, ans.chosen), "correct": ans.correct,
                                   "correct_index": shuffle.to_shown(p, it.answer), "explanation": it.explanation,
                                   "hints_used": ans.hints_used}
        else:
            q["options"] = ["True", "False"] if t == "true_false" else []
            if ans:
                said = _tf(ans.chosen) if t == "true_false" else (ans.response_text or "")
                q["response"] = {"text": said.lower() if t == "true_false" else said}
                if done:
                    k = it.key
                    q["answer"] = {"text": said, "correct": ans.correct, "explanation": it.explanation,
                                   "model_answer": _tf(it.answer) if t == "true_false" else k.get("answer", ""),
                                   "feedback": ans.feedback or "", "key_points": k.get("key_points", []),
                                   "hints_used": ans.hints_used}
        qs.append(q)
    clo = db.get(CLO, a.clo_id)
    out = {"id": a.id, "course_id": a.course_id, "clo_id": a.clo_id, "clo_code": clo.code, "clo_description": clo.description,
           "kind": a.kind, "status": a.status, "step_id": a.step_id, "questions": qs,
           "answered": len(answers), "total": len(a.item_ids), "score_pct": a.score_pct}
    if done:
        out["correct"] = sum(1 for x in answers.values() if x.correct)
    if a.mastery_record_id:
        rec = db.get(MasteryRecord, a.mastery_record_id)
        out["mastery"] = {"mastery_pct": rec.mastery_pct, "attained": rec.attained, "threshold": rec.threshold,
                          "attempts_counted": rec.attempts_counted, "rule_version": rec.rule_version}
    out["adaptation"] = getattr(a, "_adaptation", None)
    return out


def start_attempt(db: Session, user: User, course_id: int, clo_id: int, kind: str, step_id: int | None,
                  purposes: tuple[str, ...], unseen_only: bool = False) -> QuizAttempt:
    existing = db.scalar(select(QuizAttempt).where(QuizAttempt.student_id == user.id, QuizAttempt.clo_id == clo_id,
                                                   QuizAttempt.status == "in_progress"))
    if existing:
        return existing
    if kind == "formative":
        # 3-5 approved written questions (the number varies per attempt), multiple choice for the rest; if either kind
        # runs short, the other fills the quiz up to QUIZ_SIZE
        attempts = db.scalar(select(func.count(QuizAttempt.id)).where(QuizAttempt.student_id == user.id, QuizAttempt.clo_id == clo_id)) or 0
        want = random.Random(f"written:{user.id}:{clo_id}:{attempts}").randint(*WRITTEN_RANGE)
        written = pick_items(db, user.id, clo_id, purposes, size=QUIZ_SIZE, unseen_only=unseen_only, course_id=course_id,
                             kind=kind, written=True)
        mcq = pick_items(db, user.id, clo_id, purposes, size=QUIZ_SIZE, unseen_only=unseen_only, course_id=course_id,
                         kind=kind, written=False)
        take_w = min(len(written), max(want, QUIZ_SIZE - len(mcq)))
        items = written[:take_w] + mcq[:QUIZ_SIZE - take_w]
    else:
        items = pick_items(db, user.id, clo_id, purposes, size=RETEACH_SIZE, unseen_only=unseen_only, course_id=course_id, kind=kind)
    if len(items) < MIN_ITEMS:
        raise HTTPException(409, "Not enough approved questions for this outcome yet - please ask your instructor to add more")
    ids = [i.id for i in items]
    random.shuffle(ids)  # question order differs per attempt too
    a = QuizAttempt(student_id=user.id, course_id=course_id, clo_id=clo_id, kind=kind, step_id=step_id, item_ids=ids)
    db.add(a)
    db.flush()
    learning(db, user, f"{kind}_started", "quiz_attempt", a.id,
             {"clo_id": clo_id, "items": a.item_ids, "written": sum(1 for i in items if _is_written(i))}, course_id=course_id)
    return a


@router.post("/start")
def start(body: StartIn, db: Session = Depends(get_db), user: User = Depends(require("learn")), _c=Depends(require_consent)):
    ensure_course_access(db, user, body.course_id)
    clo = db.get(CLO, body.clo_id)
    if not clo or clo.course_id != body.course_id:
        raise HTTPException(404, "Outcome not found")
    if body.step_id:
        s = db.get(PathStep, body.step_id)
        p = db.get(LearningPath, s.path_id) if s else None
        if not s or p.student_id != user.id or s.clo_id != clo.id:
            raise HTTPException(404, "Path step not found")
    from ..services import paths as path_svc

    if (why := path_svc.gate(db, user.id, body.course_id, clo.id)) and not db.scalar(
            select(QuizAttempt.id).where(QuizAttempt.student_id == user.id, QuizAttempt.clo_id == clo.id, QuizAttempt.status == "in_progress")):
        raise HTTPException(409, why)  # a quiz already in progress may always be finished
    a = start_attempt(db, user, body.course_id, clo.id, "formative", body.step_id, ("formative",))
    db.commit()
    return attempt_out(db, a)


@router.get("/history/{course_id}")
def history(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """The student's own finished formative / reteach quizzes in this course, newest first (for "Past quizzes").
    The diagnostic is not listed: everyone takes it, so its answers stay hidden."""
    ensure_course_access(db, user, course_id)
    rows = db.scalars(select(QuizAttempt).where(QuizAttempt.student_id == user.id, QuizAttempt.course_id == course_id,
                                                QuizAttempt.status == "completed").order_by(QuizAttempt.completed_at.desc())).all()
    out = []
    for a in rows:
        clo = db.get(CLO, a.clo_id)
        right = db.scalar(select(func.count(QuizAnswer.id)).where(QuizAnswer.attempt_id == a.id, QuizAnswer.correct.is_(True))) or 0
        out.append({"id": a.id, "kind": a.kind, "clo_id": a.clo_id, "clo_code": clo.code if clo else "", "score_pct": a.score_pct,
                    "correct": right, "total": len(a.item_ids), "completed_at": a.completed_at})
    return out


@router.get("/attempts/{aid}")
def get_attempt(aid: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    return attempt_out(db, _attempt(db, aid, user))


@router.post("/attempts/{aid}/answer")
def answer(aid: int, body: AnswerIn, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """Save (or change) the answer to one question. Nothing is marked or revealed until the quiz is submitted."""
    a = _attempt(db, aid, user)
    if a.status != "in_progress":
        raise HTTPException(409, "This quiz is finished")
    if body.item_id not in a.item_ids:
        raise HTTPException(404, "Question not in this quiz")
    it = db.get(Item, body.item_id)
    t = it.kind
    text = None
    if t == "mcq":
        if body.choice is None or not 0 <= body.choice < len(it.options):
            raise HTTPException(422, "Invalid option")
        p = shuffle.order(shuffle.quiz_key(a.id), a.item_ids.index(it.id), len(it.options), it.answer)
        chosen = shuffle.to_original(p, body.choice)  # the student answers in the shuffled order they see
        correct = chosen == it.answer
    elif t == "true_false":
        said = (body.text or "").strip().lower()
        if said not in ("true", "false"):
            raise HTTPException(422, "Choose True or False")
        chosen = 0 if said == "true" else 1
        correct = chosen == it.answer
    else:
        text = (body.text or "").strip()[:2000]
        if not text:
            raise HTTPException(422, "Type an answer first")
        from ..services import quiz_written

        chosen = -1
        # fill in the blank is checked now; a short answer is marked when the quiz is submitted
        correct = quiz_written.fill_correct(it.key, text) if t == "fill_blank" else False
    hints = 0
    try:  # US-6.2: hints requested for this question (reteach quizzes)
        from ..models.hints import HintRequest

        hints = len(db.scalars(select(HintRequest).where(HintRequest.attempt_id == a.id, HintRequest.item_id == it.id)).all())
    except ImportError:
        pass
    prev = db.scalar(select(QuizAnswer).where(QuizAnswer.attempt_id == a.id, QuizAnswer.item_id == body.item_id))
    if prev:  # changed their mind before submitting
        prev.chosen, prev.correct, prev.hints_used, prev.response_text, prev.answered_at = chosen, correct, hints, text, utcnow()
    else:
        db.add(QuizAnswer(attempt_id=a.id, item_id=it.id, item_version=it.version_no, chosen=chosen, correct=correct,
                          hints_used=hints, response_text=text))
    learning(db, user, f"{a.kind}_answer", "item", it.id, {"attempt": a.id, "hints_used": hints, "changed": bool(prev)}, course_id=a.course_id)
    db.commit()
    n = db.scalar(select(func.count(QuizAnswer.id)).where(QuizAnswer.attempt_id == a.id)) or 0
    return {"saved": True, "answered": n, "total": len(a.item_ids)}


def finish_attempt(db: Session, user: User, a: QuizAttempt) -> QuizAttempt:
    answers = db.scalars(select(QuizAnswer).where(QuizAnswer.attempt_id == a.id)).all()
    if len(answers) < len(a.item_ids):
        raise HTTPException(409, f"Answer all {len(a.item_ids)} questions first")
    short = [(x, db.get(Item, x.item_id)) for x in answers]
    short = [(x, it) for x, it in short if it.kind == "short_answer"]
    if short:  # short written answers are marked by the AI (offline: by their key points), all in one call
        from ..services import quiz_written

        marks = quiz_written.mark_short_answers(db, user, a.course_id, [(it.stem, it.key, x.response_text or "") for x, it in short])
        for (x, _), (ok, fb) in zip(short, marks):
            x.correct, x.feedback = ok, fb
    items = [{"item_id": x.item_id, "item_version": x.item_version, "correct": x.correct, "hints_used": x.hints_used} for x in answers]
    ev, rec = mastery.add_evidence(db, user, a.clo_id, "reteach" if a.kind == "reteach" else "quiz", f"quiz_attempt:{a.id}", items)
    a.status, a.completed_at, a.score_pct = "completed", utcnow(), ev.score_pct
    a.evidence_id, a.mastery_record_id = ev.id, rec.id
    learning(db, user, f"{a.kind}_completed", "quiz_attempt", a.id,
             {"score_pct": ev.score_pct, "mastery_pct": rec.mastery_pct, "attained": rec.attained}, course_id=a.course_id)
    try:  # US-4.5: adapt the learning path using the new evidence
        from ..services.adaptation import adapt_after_quiz

        a._adaptation = adapt_after_quiz(db, user, a, rec)
    except ImportError:
        pass
    return a


@router.post("/attempts/{aid}/finish")
def finish(aid: int, db: Session = Depends(get_db), user: User = Depends(require("learn")), _c=Depends(require_consent)):
    a = _attempt(db, aid, user)
    if a.status != "in_progress":
        raise HTTPException(409, "This quiz is already finished")
    a = finish_attempt(db, user, a)
    out = attempt_out(db, a)
    db.commit()
    return out


@router.get("/mastery/{course_id}")
def my_clo_mastery(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    from ..services import paths as path_svc

    ensure_course_access(db, user, course_id)
    out = []
    for clo in db.scalars(select(CLO).where(CLO.course_id == course_id).order_by(CLO.code)):
        rec = mastery.current(db, user.id, clo.id)
        n_items = len(db.scalars(select(Item.id).where(Item.clo_id == clo.id, Item.purpose == "formative", Item.status == "approved")).all())
        out.append({"clo_id": clo.id, "clo_code": clo.code, "description": clo.description, "quiz_available": n_items >= MIN_ITEMS,
                    "mastery_pct": rec.mastery_pct if rec else None, "attained": rec.attained if rec else None,
                    "threshold": rec.threshold if rec else None, "attempts_counted": rec.attempts_counted if rec else 0,
                    "locked_reason": path_svc.gate(db, user.id, course_id, clo.id)})
    return out
