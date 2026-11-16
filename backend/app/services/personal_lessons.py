"""Personalised lectures and notes (automation mode).

For each student and each CLO, the AI writes a lecture plus short revision notes pitched to that student:

  level        when                                                      the lecture
  foundations  diagnostic "not ready", or mastery well below the target  first principles, simple analogy, two fully
                                                                          worked examples, guided practice
  standard     diagnostic "developing", no data yet, or mastery a        full explanation, worked examples,
               little below the target                                    common mistakes, a practice question
  extension    diagnostic "ready", or mastery at/above the target        concise recap, a challenging example,
                                                                          links to other outcomes, an extension idea

The context is the course outline (CLOs and the PLOs they map to); approved teaching sources are added and cited
when they match. Lessons are stored per (student, CLO, level) so the material is stable; when the learner's level
changes, a new lesson is written. Learners can report a lesson (it is an AI call) and it goes to the review queue.
If the AI is unavailable an outcome-based study note is shown and nothing is stored, so the next visit retries.

Topic lessons: when the course has topics, a lesson is written per (student, topic, level) and teaches only that
topic, placed between the topics before and after it. An instructor can edit a student's lesson or have it rewritten
with a note (e.g. "use a cooking analogy, she struggles with loops"); the note goes into the prompt.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.audit import learning
from ..models.content import StudentLesson
from ..models.outcomes import CLO, Course
from ..models.topics import Topic
from . import ai_gateway, course_context, mastery, retrieval
from .diagnostic import course_readiness, latest_readiness

LEVEL_LABEL = {"foundations": "Foundations", "standard": "Core", "extension": "Stretch"}
GUIDE = {
    "foundations": (
        "This learner is NOT READY for this outcome yet. Start from first principles: define every term in plain "
        "language, use one simple everyday analogy, then give TWO small, fully worked examples with every step "
        "explained, then ONE guided practice question followed by its worked solution. Short sentences."
    ),
    "standard": (
        "This learner is DEVELOPING this outcome. Give a complete explanation of the key ideas, two worked examples of "
        "increasing difficulty, the most common mistakes and how to avoid them, and ONE practice question followed "
        "by its worked solution."
    ),
    "extension": (
        "This learner is READY for this outcome. Give a concise recap of the key ideas, ONE challenging worked "
        "example, how this outcome connects to the other outcomes of the course, and ONE extension idea to explore."
    ),
}
SYSTEM = (
    "You are an expert university lecturer writing a personal lecture for ONE learner. Teach the given learning "
    "outcome (or topic) at its Bloom level, within the scope of the COURSE OUTLINE, pitched exactly as the LEARNER "
    "PROFILE says. The lecture must be COMPREHENSIVE - a full university class, about 1500-2500 words - with these "
    "## sections: Why this matters; Key ideas (each concept explained in depth with definitions and intuition); "
    "Worked examples (2-3, every step explained); a Mermaid diagram that shows how the ideas connect (described in one "
    "sentence); Common mistakes and how to avoid them; Where it is used (real-world or later courses); Check your "
    "understanding (3 questions, answers in a collapsed <details> block); Summary. If numbered SOURCES are provided, "
    "base the lecture on them and cite them like [S1]. Use Markdown with short paragraphs, fenced code blocks with a "
    "language tag, LaTeX maths in $...$ or $$...$$. Do not write quiz answer keys. "
    "ALSO present the same lecture as 8-15 SLIDES: each slide has a title, 2-5 short points, an optional visual "
    "(a ```mermaid diagram, a code block, a small table or $$maths$$ - at least 3 slides must have a visual) and an "
    "explanation of 80-160 words that a lecturer would say for that slide. "
    'Return JSON: {"lecture_md": "the full lecture", "notes_md": "5-8 bullet-point revision notes", '
    '"slides": [{"title": "...", "points": ["..."], "visual": "...", "explanation": "..."}]}'
)


def clean_slides(raw) -> list[dict]:
    """Keep well-formed slides only (title + points or explanation); cap points and the deck size."""
    out = []
    for sl in raw if isinstance(raw, list) else []:
        if not isinstance(sl, dict):
            continue
        title = str(sl.get("title") or "").strip()
        points = [str(p).strip() for p in (sl.get("points") or []) if str(p).strip()][:6]
        expl = str(sl.get("explanation") or "").strip()
        if title and (points or expl):
            out.append({"title": title[:160], "points": points, "visual": str(sl.get("visual") or "").strip(), "explanation": expl})
    return out[:20]


def level_for(db: Session, student_id: int, course_id: int, clo: CLO) -> tuple[str, str]:
    """(level, reason) from current mastery if the learner has quiz evidence, otherwise the diagnostic."""
    rec = mastery.current(db, student_id, clo.id)
    if rec is not None:
        if rec.mastery_pct >= rec.threshold:
            return "extension", f"mastery {rec.mastery_pct:.0f}% (target {rec.threshold:.0f}%)"
        if rec.mastery_pct >= rec.threshold - 20:
            return "standard", f"mastery {rec.mastery_pct:.0f}% (target {rec.threshold:.0f}%)"
        return "foundations", f"mastery {rec.mastery_pct:.0f}% (target {rec.threshold:.0f}%)"
    ready, info = course_readiness(db, student_id, course_id)
    r = ready.get(clo.id)
    if r is not None:
        level = {"ready": "extension", "developing": "standard"}.get(r.level, "foundations")
        what = f"prerequisite result ({info['names']})" if info["source"] == "prerequisite" else "diagnostic"
        return level, f"{what} {r.score_pct:.0f}% ({r.level.replace('_', ' ')})"
    return "standard", "no diagnostic result yet"


def _profile(db: Session, student_id: int, course_id: int, clo: CLO, level: str, reason: str) -> str:
    weak = []
    for other_id, r in latest_readiness(db, student_id, course_id).items():
        if other_id != clo.id and r.level == "not_ready":
            o = db.get(CLO, other_id)
            if o:
                weak.append(f"{o.code} ({o.description})")
    return (f"LEARNER PROFILE:\nLevel for {clo.code}: {LEVEL_LABEL[level]} - based on {reason}.\n{GUIDE[level]}\n"
            + (f"The learner is also weak on: {'; '.join(weak)}. Briefly recap anything from those that this outcome needs.\n" if weak else ""))


def _offline(clo: CLO, level: str) -> dict:
    lecture = (f"## {clo.code}: {clo.description}\n\n> The AI lecturer is offline right now, so here is a short study "
               "guide. Your personal lecture will be written on your next visit.\n\n"
               "1. Read the outcome above and underline its key words.\n2. For each key word, write a one-line definition.\n"
               "3. Work through an example from your course material step by step.\n4. Try the practice questions below, then the formative quiz.")
    notes = f"- Outcome: {clo.description}\n- Level: {LEVEL_LABEL[level]}\n- Next: practice questions, then the formative quiz for {clo.code}"
    return {"lecture_md": lecture, "notes_md": notes}


def out(lesson: StudentLesson | None, clo: CLO, level: str, reason: str, data: dict | None = None, topic: Topic | None = None) -> dict:
    d = data or {"lecture_md": lesson.lecture_md, "notes_md": lesson.notes_md, "slides": lesson.slides or []}
    d.setdefault("slides", [])
    return {"id": lesson.id if lesson else None, "topic_id": topic.id if topic else None, "topic_title": topic.title if topic else None,
            "topic_number": topic.number if topic else None,
            "edited_by_instructor": bool(lesson and (lesson.edited_by or lesson.instructor_note)),
            "clo_id": clo.id, "clo_code": clo.code, "description": clo.description, "level": level,
            "level_label": LEVEL_LABEL[level], "reason": reason, "origin": lesson.origin if lesson else "offline",
            "ai_call_id": lesson.ai_call_id if lesson else None, "created_at": lesson.created_at if lesson else None,
            "cited_sources": bool(lesson and lesson.source_chunk_ids), **d}


def _topic_block(db: Session, course: Course, topic: Topic) -> str:
    from . import topic_planner

    ts = topic_planner.topics(db, course.id)
    i = next((k for k, t in enumerate(ts) if t.id == topic.id), 0)
    before = "; ".join(t.title for t in ts[max(0, i - 2):i])
    after = "; ".join(t.title for t in ts[i + 1:i + 3])
    return (f"TOPIC {topic.number} of {len(ts)}: {topic.title}\n{topic.summary}\n"
            f"Teach ONLY this topic, fully and with depth (it is one week of a university course)."
            + (f" Earlier topics already covered: {before}." if before else "")
            + (f" Later topics (do not teach yet): {after}." if after else "") + "\n\n")


def get_or_create(db: Session, student, course: Course, clo: CLO, refresh: bool = False, topic: Topic | None = None,
                  note: str | None = None, actor=None) -> dict:
    """The student's lesson for a CLO (or one topic of it). `note` (instructor) asks for a rewrite with guidance;
    `actor` is who triggers the AI call (the instructor for a rewrite, else the student)."""
    level, reason = level_for(db, student.id, course.id, clo)
    q = select(StudentLesson).where(StudentLesson.student_id == student.id, StudentLesson.clo_id == clo.id,
                                    StudentLesson.level == level)
    q = q.where(StudentLesson.topic_id == topic.id) if topic else q.where(StudentLesson.topic_id.is_(None))
    existing = db.scalar(q.order_by(StudentLesson.id.desc()).limit(1))
    if existing and not refresh and not note:
        return out(existing, clo, level, reason, topic=topic)
    hits = retrieval.search(db, course.id, f"{topic.title if topic else ''} {clo.description} {clo.code}", k=4)
    sources = f"SOURCES:\n{ai_gateway.format_sources(hits)}\n\n" if hits else ""
    prompt = (f"COURSE OUTLINE:\n{course_context.outline(db, course)}\n\n{sources}"
              f"LEARNING OUTCOME {clo.code}: {clo.description}" + (f" (Bloom level: {clo.bloom})" if clo.bloom else "") +
              f"\n\n{_topic_block(db, course, topic) if topic else ''}{_profile(db, student.id, course.id, clo, level, reason)}"
              + ("\nWrite it DIFFERENTLY from the previous version (new analogy and new examples).\n" if (refresh or note) and existing else "")
              + (f"\nINSTRUCTOR NOTE (follow it): {note.strip()}\n" if note else ""))
    try:
        from .schemas import LessonOut

        # schema-checked like every reply; temperature stays above 0 on purpose - a lecture is writing, not
        # extraction, and "write it differently" (refresh) needs some variety
        data = ai_gateway.call_json(db, actor or student, "lesson.topic" if topic else "lesson.personal", prompt, SYSTEM,
                                    temperature=0.5, course_id=course.id, max_output_tokens=16384,
                                    # a full lesson plus slides is long: allow more time than a short reply
                                    budget=max(90.0, get_settings().gemini_total_timeout_seconds),
                                    chunk_ids=[h.chunk_id for h in hits], schema=LessonOut)
        data = ai_gateway.as_object(data)
        lecture, notes = str(data.get("lecture_md") or "").strip(), str(data.get("notes_md") or "").strip()
        slides = clean_slides(data.get("slides"))
        if len(lecture) < 80:
            raise ai_gateway.AIUnavailable("model returned no usable lecture")
        # never show text that leaks an assessment answer pattern ("the correct answer is B")
        if ai_gateway.protect(lecture + " ".join(s["explanation"] for s in slides), [])[1]:
            raise ai_gateway.AIUnavailable("lecture tripped the answer-protection guard")
    except ai_gateway.AIUnavailable:
        if note:
            raise
        offline = _offline(clo, level)
        if topic:  # the topic page shows its own "not ready yet" notice, so the guide starts with the topic itself
            steps = offline["lecture_md"].split("\n\n", 2)[-1]
            offline["lecture_md"] = f"## {topic.title}\n\n{topic.summary}\n\n{steps}"
        if hits:  # AI offline: teach from the course's own approved reading instead
            offline["lecture_md"] += "\n\n## From your suggested reading\n\n" + "\n\n".join(
                f"### {h.heading or h.source_title}\n\n{h.text}\n\n*Source: {h.source_title}*" for h in hits[:3])
        return out(None, clo, level, reason, offline, topic=topic)
    lesson = StudentLesson(student_id=student.id, course_id=course.id, clo_id=clo.id, level=level, lecture_md=lecture,
                           notes_md=notes or "", origin="ai", source_chunk_ids=[h.chunk_id for h in hits],
                           ai_call_id=ai_gateway.last_call_id(db), topic_id=topic.id if topic else None,
                           instructor_note=note.strip() if note else None, edited_by=getattr(actor, "id", None) if note else None,
                           slides=slides or None)
    db.add(lesson)
    db.flush()
    learning(db, student, "personal_lesson_created", "student_lesson", lesson.id,
             {"clo_id": clo.id, "topic_id": topic.id if topic else None, "level": level, "refresh": refresh,
              "instructor_note": bool(note)}, course_id=course.id)
    return out(lesson, clo, level, reason, topic=topic)
