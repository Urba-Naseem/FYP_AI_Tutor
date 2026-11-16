"""US-6.3 - Reteach below mastery.

  Explanation  An approved alternate explanation for the CLO (US-5.4) if one exists;
               otherwise a Gemini explanation from the CLO and course outline, enriched with approved sources when they exist (routed
               to the instructor as a suggested alternate); offline: extracts from the
               approved sources that differ from the main lesson.
  Questions    Each reteach quiz uses questions the learner has NOT seen before
               (reteach items first, then unseen formative items).
  Limit        MAX_ATTEMPTS (3) reteach quizzes per cycle.
  Advancement  R-PASS      attained                      -> cycle passed, move on
               R-RETRY     below, attempts left          -> same step, new questions
               R-EXHAUSTED below, no attempts left       -> learner moves on (not trapped),
                                                            step flagged and escalated (US-6.4)
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning
from ..db import utcnow
from ..models.content import ContentDoc, ContentVersion
from ..models.outcomes import CLO
from ..models.reteach import ReteachCycle
from . import ai_gateway, retrieval

MAX_ATTEMPTS = 3
RULES_VERSION = "reteach-rules/v1"

EXPLAIN_SYSTEM = (
    "You are a patient tutor helping a learner who is struggling with an outcome. Explain it DIFFERENTLY from a lecture: "
    "an everyday analogy, then a short step-by-step worked example, then one common misconception. About 250 words, "
    "Markdown, at the level of the outcome. If numbered SOURCES are provided, use them and cite them like [S1]. Do not include any quiz answers."
)


def active_cycle(db: Session, student_id: int, clo_id: int) -> ReteachCycle | None:
    return db.scalar(select(ReteachCycle).where(ReteachCycle.student_id == student_id, ReteachCycle.clo_id == clo_id,
                                                ReteachCycle.status == "active"))


def _approved_alternate(db: Session, clo_id: int) -> ContentVersion | None:
    doc = db.scalar(select(ContentDoc).where(ContentDoc.clo_id == clo_id, ContentDoc.kind == "alternate"))
    if not doc:
        return None
    return db.scalar(select(ContentVersion).where(ContentVersion.doc_id == doc.id, ContentVersion.status == "approved")
                     .order_by(ContentVersion.version_no.desc()).limit(1))


def recent_mistakes(db: Session, student_id: int, clo_id: int, limit: int = 4) -> list[str]:
    """This student's wrong answers in their latest finished quiz on the outcome (question, their answer, the right one).
    They have already seen the right answers as feedback, so nothing new is revealed."""
    from ..models.items import Item
    from ..models.quiz import QuizAnswer, QuizAttempt

    a = db.scalar(select(QuizAttempt).where(QuizAttempt.student_id == student_id, QuizAttempt.clo_id == clo_id,
                                            QuizAttempt.status == "completed").order_by(QuizAttempt.id.desc()).limit(1))
    if not a:
        return []
    out = []
    for ans in db.scalars(select(QuizAnswer).where(QuizAnswer.attempt_id == a.id, QuizAnswer.correct.is_(False))):
        it = db.get(Item, ans.item_id)
        if not it:
            continue
        try:
            out.append(f"- Question: {it.stem}\n  Their answer: {it.options[ans.chosen]}\n  Correct answer: {it.options[it.answer]}")
        except (IndexError, TypeError):
            continue
        if len(out) >= limit:
            break
    return out


def open_cycle(db: Session, student, course_id: int, clo: CLO) -> ReteachCycle:
    cyc = active_cycle(db, student.id, clo.id)
    if cyc:
        return cyc
    alt = _approved_alternate(db, clo.id)
    call_id = None
    mistakes = recent_mistakes(db, student.id, clo.id)
    profile = ("THIS LEARNER'S LAST ANSWERS THAT WERE WRONG (explain the misunderstanding behind each, then the idea, "
               "without quizzing them on these exact questions):\n" + "\n".join(mistakes) + "\n\n") if mistakes else ""
    if alt:
        # the teacher-approved explanation stays the core; a short personal note about this learner's own mistakes follows
        md, source = alt.body_md, "approved_alternate"
        if mistakes:
            try:
                note = ai_gateway.call_text(db, student, "reteach.personal_note", f"OUTCOME {clo.code}: {clo.description}\n\n{profile}"
                                            "Write a short section (under 150 words) that explains what went wrong in these answers.",
                                            EXPLAIN_SYSTEM, temperature=0.4, course_id=course_id)
                md = f"{md}\n\n### About your last answers\n\n{note}"
                call_id = ai_gateway.last_call_id(db)
            except ai_gateway.AIUnavailable:
                pass
    else:
        # Written from the CLO and the course outline; approved teaching sources are added when they match.
        from ..models.outcomes import Course
        from . import course_context

        hits = retrieval.search(db, course_id, clo.description, k=5)
        sources = f"SOURCES:\n{ai_gateway.format_sources(hits)}\n\n" if hits else ""
        prompt = (f"COURSE OUTLINE:\n{course_context.outline(db, db.get(Course, course_id), [clo])}\n\n{sources}"
                  f"{profile}OUTCOME {clo.code}: {clo.description}\nExplain this outcome"
                  + (", starting from the mistakes above." if mistakes else "."))
        try:
            md = ai_gateway.call_text(db, student, "reteach.explain", prompt, EXPLAIN_SYSTEM, temperature=0.5,
                                      course_id=course_id, chunk_ids=[h.chunk_id for h in hits])
            source = "ai_grounded" if hits else "ai_curriculum"
            call_id = ai_gateway.last_call_id(db)
            ai_gateway.route_review(db, "tutor_answer", "ai_call", call_id, course_id,
                                    f"AI reteach explanation for {clo.code} shown to a learner - consider approving an alternate explanation",
                                    student, call_id)
        except ai_gateway.AIUnavailable:
            if hits:
                picked = hits[1:4] or hits[:1]  # passages other than the best match used by the main lesson
                md = (f"### {clo.code} - another look\n\n> Offline mode: extracts from your approved course notes.\n\n"
                      + "\n\n".join(f"**{h.heading or h.source_title}**\n\n{h.text}\n\n*Source: {h.source_title}*" for h in picked))
                source = "offline_extract"
            else:
                md = (f"### {clo.code} - another look\n\n> The AI tutor is offline right now.\n\n"
                      f"This outcome asks you to: **{clo.description}**\n\nRe-read the unit lesson for it, then try the new "
                      "questions. Break the outcome into its key words and check you can explain each one.")
                source = "offline_note"
    cyc = ReteachCycle(student_id=student.id, course_id=course_id, clo_id=clo.id, max_attempts=MAX_ATTEMPTS,
                       explanation_source=source, explanation_md=md, ai_call_id=call_id)
    db.add(cyc)
    db.flush()
    learning(db, student, "reteach_started", "reteach_cycle", cyc.id, {"clo_id": clo.id, "explanation": source}, course_id=course_id)
    return cyc


def after_reteach_quiz(db: Session, student, attempt, rec, path, steps, current) -> dict:
    """Apply the reteach advancement rules. Returns (rule, decision, message, new step dicts or None)."""
    from . import paths as path_svc

    cyc = active_cycle(db, student.id, attempt.clo_id)
    clo = db.get(CLO, attempt.clo_id)
    thr = f"{rec.threshold:.0f}%"
    new_steps = [path_svc.step_dict(s) for s in steps]
    idx = steps.index(current) if current in steps else None
    if rec.attained:
        rule, decision = "R-PASS", "advance"
        msg = f"{clo.code} attained after reteaching ({rec.mastery_pct:.0f}% ≥ {thr}). Moving on."
        if cyc:
            cyc.status, cyc.closed_at = "passed", utcnow()
        for d_, s in zip(new_steps, steps):
            if s.clo_id == clo.id and s.status == "pending" and s.step_type in ("reteach", "practice", "review"):
                d_["status"], d_["completed_at"] = "done", utcnow()
    elif cyc and cyc.attempts_used < cyc.max_attempts:
        rule, decision = "R-RETRY", "retry"
        left = cyc.max_attempts - cyc.attempts_used
        msg = f"{clo.code} is at {rec.mastery_pct:.0f}% (target {thr}). You have {left} more reteach attempt(s) with new questions."
        new_steps = None  # path unchanged: stay on the reteach step
    else:
        rule, decision = "R-EXHAUSTED", "advance_flagged"
        msg = (f"You've used all {MAX_ATTEMPTS} reteach attempts for {clo.code}. You can carry on with your path, and your "
               "instructor will follow up with you on this outcome.")
        if cyc:
            cyc.status, cyc.closed_at = "exhausted", utcnow()
        if idx is not None:
            new_steps[idx]["status"], new_steps[idx]["completed_at"] = "skipped", utcnow()
            new_steps[idx]["meta"] = {**new_steps[idx]["meta"], "flag": "needs_instructor", "outcome": f"{rec.mastery_pct:.0f}% after {MAX_ATTEMPTS} attempts"}
    return {"rule": rule, "decision": decision, "message": msg, "steps": new_steps, "cycle": cyc}
