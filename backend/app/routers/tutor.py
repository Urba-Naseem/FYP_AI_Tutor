"""In-lesson Socratic tutor (topic pages) and the student's saved notes / flashcards.

POST /api/tutor/{course}/topics/{topic}   mode = explain | check | answer
    explain  explain the highlighted text (or a question) simply, with a brief prerequisite recap when needed,
             citing the exact lesson section; ends with one guiding question
    check    ask ONE Socratic question about the lesson (never the answer)
    answer   the student's reply to a check: confirm, or give a hint and a follow-up question - never the full
             answer straight away
Every reply cites lesson sections as numbers (the page turns them into links) and may offer one flashcard.
GET/POST/DELETE /api/tutor/{course}/notes  the student's own notes and flashcards (per topic).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning
from ..core.rbac import ensure_course_access, require
from ..db import get_db
from ..models.content import StudentLesson
from ..models.outcomes import CLO
from ..models.topics import StudentNote, Topic
from ..models.user import User
from ..services import ai_gateway

router = APIRouter(prefix="/api/tutor", tags=["tutor"])

SYSTEM = (
    "You are a Socratic, context-aware AI tutor embedded in a lecture reader. Help the student understand and "
    "retain the ACTIVE LESSON without breaking their reading flow. Explain highlighted text, code or formulas simply; "
    "give a brief prerequisite recap only when needed; run Socratic one-question checks and guide with follow-up "
    "questions. RULES: never give a full answer immediately - ask a guiding question and offer a hint first; always "
    "cite the exact lesson section(s) you used by their numbers; keep replies brief and scannable (short paragraphs "
    "or bullets, at most about 150 words); stay inside the lesson and course. Use Markdown, LaTeX in $...$, fenced code."
)


class Card(BaseModel):
    front: str
    back: str


class TutorOut(BaseModel):
    reply_md: str = Field(description="The tutor's reply in Markdown (brief).")
    cited_sections: list[int] = Field(default_factory=list, description="Numbers of the lesson sections used.")
    question: str = Field(default="", description="For a check: the one question asked (empty otherwise).")
    flashcard: Card | None = Field(default=None, description="Optional: one flashcard capturing the key idea.")


class Turn(BaseModel):
    role: str
    text: str


class AskIn(BaseModel):
    mode: str
    selection: str | None = None
    message: str | None = None
    history: list[Turn] = []


def sections(md: str) -> list[dict]:
    """Lesson split at '## ' headings (outside code blocks): [{n, title, text}]."""
    out, cur, fenced = [], {"n": 0, "title": "Introduction", "text": []}, False
    for line in md.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        m = None if fenced else re.match(r"^##\s+(.*)", line)
        if m:
            if "".join(cur["text"]).strip():
                out.append(cur)
            cur = {"n": len(out) + 1, "title": m.group(1).strip(), "text": []}
        else:
            cur["text"].append(line)
    if "".join(cur["text"]).strip():
        out.append(cur)
    for i, s in enumerate(out, start=1):
        s["n"], s["text"] = i, "\n".join(s["text"]).strip()
    return out


def _lesson(db: Session, user: User, course_id: int, topic_id: int) -> tuple[Topic, list[dict]]:
    ensure_course_access(db, user, course_id)
    t = db.get(Topic, topic_id)
    if not t or t.course_id != course_id:
        raise HTTPException(404, "Topic not found")
    ls = db.scalar(select(StudentLesson).where(StudentLesson.student_id == user.id, StudentLesson.topic_id == topic_id)
                   .order_by(StudentLesson.id.desc()).limit(1))
    body = ls.lecture_md if ls else f"## {t.title}\n\n{t.summary}"
    return t, sections(body)


def _best_section(secs: list[dict], text: str) -> dict | None:
    words = {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}
    if not secs or not words:
        return secs[0] if secs else None
    return max(secs, key=lambda s: len(words & set(re.findall(r"[a-z0-9]{3,}", s["text"].lower()))))


@router.post("/{course_id}/topics/{topic_id}")
def ask(course_id: int, topic_id: int, body: AskIn, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    if body.mode not in ("explain", "check", "answer", "hint", "reveal"):
        raise HTTPException(422, "mode must be explain, check, answer, hint or reveal")
    if body.mode == "reveal" and not any(h.role == "student" and h.text.startswith("Answer:") for h in body.history):
        raise HTTPException(409, "Try an answer first - the answer is revealed only after an attempt")
    if body.mode == "explain" and not (body.selection or body.message or "").strip():
        raise HTTPException(422, "Highlight some text in the lesson or type a question")
    if body.mode == "answer" and not (body.message or "").strip():
        raise HTTPException(422, "Type your answer first")
    t, secs = _lesson(db, user, course_id, topic_id)
    clo = db.get(CLO, t.clo_ids[0]) if t.clo_ids else None
    lesson = "\n\n".join(f"[SECTION {s['n']}: {s['title']}]\n{s['text'][:2500]}" for s in secs)
    history = "\n".join(f"{h.role.upper()}: {h.text[:600]}" for h in body.history[-8:])
    task = {
        "explain": f"The student highlighted: \"\"\"{(body.selection or '')[:1200]}\"\"\"\n"
                   + (f"Their question: {body.message[:600]}\n" if body.message else "")
                   + "Explain it simply (a brief prerequisite recap only if needed), cite the section(s), then end with ONE "
                     "short guiding question to check they got it. Offer one flashcard of the key idea.",
        "check": ("Ask ONE Socratic question that checks understanding of "
                  + (f"this highlighted part: \"\"\"{body.selection[:800]}\"\"\"" if body.selection else "an important idea of this lesson")
                  + ". Put the question in `question` and in the reply. Do NOT give the answer or hint at it."),
        "hint": "Give ONE short hint for your last question (point to the right section), without the answer. Ask the student to try again.",
        "reveal": "The student has tried. Now give the correct answer to your last question briefly, explain why, and cite the section.",
        "answer": f"The student answered your last question: \"\"\"{(body.message or '')[:800]}\"\"\"\n"
                  "If correct: confirm in one line and say why, citing the section. If partly correct or wrong: do NOT give "
                  "the full answer - give one hint pointing to the right section and ask a simpler follow-up question.",
    }[body.mode]
    prompt = (f"TOPIC: {t.title}" + (f" (outcome {clo.code}: {clo.description})" if clo else "") +
              f"\n\nACTIVE LESSON (numbered sections):\n{lesson}\n\n" + (f"CONVERSATION SO FAR:\n{history}\n\n" if history else "") + f"TASK: {task}")
    try:
        data = ai_gateway.as_object(ai_gateway.call_json(db, user, f"tutor.{body.mode}", prompt, SYSTEM, temperature=0.4,
                                                         course_id=course_id, schema=TutorOut, max_output_tokens=2048))
        reply = str(data.get("reply_md") or "").strip()
        if not reply:
            raise ai_gateway.AIUnavailable("empty reply")
    except ai_gateway.AIUnavailable:
        db.commit()
        if body.mode != "explain":
            raise HTTPException(503, "The tutor is offline right now. Try again in a few minutes.")
        s = _best_section(secs, body.selection or body.message or "")
        return {"reply_md": "The AI tutor is offline right now. This part of your lesson covers what you highlighted:\n\n"
                            + (f"**{s['title']}**\n\n{s['text'][:900]}" if s else ""),
                "cited": [{"n": s["n"], "title": s["title"]}] if s else [], "question": "", "flashcard": None, "offline": True}
    valid = {s["n"]: s["title"] for s in secs}
    cited = [{"n": n, "title": valid[n]} for n in dict.fromkeys(int(x) for x in (data.get("cited_sections") or []) if str(x).isdigit()) if n in valid]
    card = data.get("flashcard") if isinstance(data.get("flashcard"), dict) and data["flashcard"].get("front") else None
    learning(db, user, f"tutor_{body.mode}", "topic", t.id, {"cited": [c["n"] for c in cited]}, course_id=course_id)
    db.commit()
    return {"reply_md": reply, "cited": cited, "question": str(data.get("question") or ""),
            "flashcard": {"front": str(card["front"])[:300], "back": str(card.get("back") or "")[:600]} if card else None, "offline": False}


class NoteIn(BaseModel):
    topic_id: int | None = None
    kind: str = "note"  # note | flashcard
    front: str
    back: str = ""
    section: str | None = None


def _note_out(n: StudentNote) -> dict:
    return {"id": n.id, "topic_id": n.topic_id, "kind": n.kind, "front": n.front, "back": n.back, "section": n.section,
            "created_at": n.created_at}


@router.get("/{course_id}/notes")
def notes(course_id: int, topic_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    ensure_course_access(db, user, course_id)
    q = select(StudentNote).where(StudentNote.student_id == user.id, StudentNote.course_id == course_id)
    if topic_id:
        q = q.where(StudentNote.topic_id == topic_id)
    return [_note_out(n) for n in db.scalars(q.order_by(StudentNote.id.desc()))]


@router.post("/{course_id}/notes", status_code=201)
def add_note(course_id: int, body: NoteIn, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    ensure_course_access(db, user, course_id)
    if body.kind not in ("note", "flashcard") or not body.front.strip():
        raise HTTPException(422, "A note needs some text")
    if body.topic_id:
        t = db.get(Topic, body.topic_id)
        if not t or t.course_id != course_id:
            raise HTTPException(404, "Topic not found")
    n = StudentNote(student_id=user.id, course_id=course_id, topic_id=body.topic_id, kind=body.kind,
                    front=body.front.strip()[:4000], back=body.back.strip()[:2000], section=(body.section or "")[:200] or None)
    db.add(n)
    db.commit()
    return _note_out(n)


@router.delete("/{course_id}/notes/{nid}")
def delete_note(course_id: int, nid: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    n = db.get(StudentNote, nid)
    if not n or n.student_id != user.id or n.course_id != course_id:
        raise HTTPException(404, "Note not found")
    db.delete(n)
    db.commit()
    return {"ok": True}
