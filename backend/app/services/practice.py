"""Practice questions before a formative quiz (ungraded).

* The AI writes a short set (5 by default) for one student and one CLO - or one topic of it - pitched to the
  student's level (same levels as their personal lessons) and avoiding stems they have already practised.
* Practice never counts toward mastery and never touches the approved question bank, so formative and reteach
  quizzes stay unseen. Students may ask for as many new sets as they like.
* Each topic (lecture) gets its own questions: the set is written for that topic only, avoids stems the student has
  already practised on the outcome, and the page restores the latest set of THAT topic.
* Every question has up to 2 Socratic hints the student can open one at a time; hints never contain the answer.
* The answer and explanation of a question are sent only after the student answers it.
* No AI, no practice: if the AI is unavailable the student is told to try again later (nothing is stored).
"""
from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning
from ..models.outcomes import CLO, Course
from ..models.topics import PracticeSet, Topic
from . import ai_gateway, course_context
from .personal_lessons import LEVEL_LABEL, level_for

SET_SIZE = 5
AVOID = 40  # recent stems the AI is told not to repeat
SYSTEM = (
    "You write multiple-choice PRACTICE questions for one university learner. Each question tests understanding of "
    "the given outcome (and topic, when given) at the learner's level, has exactly 4 plausible options with one "
    "correct answer, and a short explanation of why the correct option is right and the others are wrong. Give each "
    "question 2 short Socratic hints (first a nudge towards the key idea, then a more specific pointer) that NEVER state, "
    "quote or eliminate options. When a TOPIC is given, every question must be about that topic specifically. "
    "Vary the question types (concept, worked example, spot the error, predict the output). Never repeat a question "
    "from the AVOID list."
)
LEVEL_HINT = {"foundations": "Keep questions basic: definitions, simple recognition, one-step examples.",
              "standard": "Mix basic and applied questions with short worked examples.",
              "extension": "Make questions challenging: multi-step reasoning, edge cases, comparisons."}


class PracticeUnavailable(Exception):
    pass


def _recent_stems(db: Session, student_id: int, clo_id: int) -> list[str]:
    sets = db.scalars(select(PracticeSet).where(PracticeSet.student_id == student_id, PracticeSet.clo_id == clo_id)
                      .order_by(PracticeSet.id.desc()).limit(10)).all()
    return [q["stem"] for s in sets for q in (s.questions or [])][:AVOID]


def _clean(raw: list[dict], avoid: set[str]) -> list[dict]:
    out, seen = [], set(avoid)
    for q in raw:
        stem = str(q.get("stem") or "").strip()
        opts = [str(o).strip() for o in (q.get("options") or [])]
        try:
            ans = int(q.get("answer_index"))
        except (TypeError, ValueError):
            continue
        key = stem.lower()
        if not stem or len(opts) != 4 or len(set(o.lower() for o in opts)) != 4 or not 0 <= ans < 4 or key in seen:
            continue
        seen.add(key)
        correct = opts[ans].lower()
        hints = [str(h).strip() for h in (q.get("hints") or []) if str(h).strip()]
        hints = [h for h in hints if correct not in h.lower()][:2]  # a hint may never give the answer away
        # shuffle the options so the correct answer is not always in the same place
        order = list(range(4))
        random.shuffle(order)
        out.append({"stem": stem, "options": [opts[i] for i in order], "answer_index": order.index(ans),
                    "explanation": str(q.get("explanation") or "").strip(), "hints": hints})
    return out


def new_set(db: Session, student, course: Course, clo: CLO, topic: Topic | None = None, n: int = SET_SIZE) -> PracticeSet:
    from .schemas import PracticeOut

    level, reason = level_for(db, student.id, course.id, clo)
    avoid = _recent_stems(db, student.id, clo.id)
    prompt = (f"{course_context.outline(db, course, focus=[clo])}\n\nOUTCOME {clo.code}: {clo.description}\n"
              + (f"TOPIC (write every question about this lecture topic only): {topic.title} - {topic.summary}\n" if topic else "")
              + f"LEARNER LEVEL: {LEVEL_LABEL[level]} ({reason}). {LEVEL_HINT[level]}\n"
              + f"Write {n} practice questions.\n"
              + ("AVOID (already practised):\n" + "\n".join(f"- {a}" for a in avoid) if avoid else ""))
    try:
        data = ai_gateway.as_object(ai_gateway.call_json(db, student, "practice.questions", prompt, SYSTEM, temperature=0.7,
                                                         course_id=course.id, schema=PracticeOut))
    except ai_gateway.AIUnavailable as e:
        raise PracticeUnavailable(str(e)) from e
    qs = _clean(data.get("questions") or [], {a.lower() for a in avoid})
    if not qs:
        raise PracticeUnavailable("the AI returned no usable questions")
    ps = PracticeSet(student_id=student.id, course_id=course.id, clo_id=clo.id, topic_id=topic.id if topic else None,
                     questions=qs, answers={}, ai_call_id=ai_gateway.last_call_id(db))
    db.add(ps)
    db.flush()
    learning(db, student, "practice_set_created", "practice_set", ps.id,
             {"clo_id": clo.id, "topic_id": ps.topic_id, "questions": len(qs), "level": level}, course_id=course.id)
    return ps


def out(ps: PracticeSet) -> dict:
    """What the student may see: answers and explanations only for questions already answered."""
    qs = []
    for i, q in enumerate(ps.questions or []):
        item = {"index": i, "stem": q["stem"], "options": q["options"], "hints": q.get("hints") or []}
        chosen = (ps.answers or {}).get(str(i))
        if chosen is not None:
            item.update(chosen=chosen, correct=chosen == q["answer_index"], correct_index=q["answer_index"],
                        explanation=q["explanation"])
        qs.append(item)
    answered = [q for q in qs if "chosen" in q]
    return {"id": ps.id, "clo_id": ps.clo_id, "topic_id": ps.topic_id, "created_at": ps.created_at, "questions": qs, "answered": len(answered),
            "correct": sum(1 for q in answered if q["correct"]), "total": len(qs), "counts_toward_mastery": False}


def answer(db: Session, student, ps: PracticeSet, index: int, choice: int) -> dict:
    if not 0 <= index < len(ps.questions or []):
        raise ValueError("No such question")
    if not 0 <= choice < 4:
        raise ValueError("Choose one of the four options")
    if str(index) not in (ps.answers or {}):
        ps.answers = {**(ps.answers or {}), str(index): choice}
        q = ps.questions[index]
        learning(db, student, "practice_answer", "practice_set", ps.id,
                 {"index": index, "correct": choice == q["answer_index"]}, course_id=ps.course_id)
    return out(ps)


def latest(db: Session, student_id: int, clo_id: int, topic_id: int | None) -> PracticeSet | None:
    """The student's most recent set for this topic (or for the outcome as a whole when no topic)."""
    q = select(PracticeSet).where(PracticeSet.student_id == student_id, PracticeSet.clo_id == clo_id)
    q = q.where(PracticeSet.topic_id == topic_id) if topic_id else q.where(PracticeSet.topic_id.is_(None))
    return db.scalar(q.order_by(PracticeSet.id.desc()).limit(1))
