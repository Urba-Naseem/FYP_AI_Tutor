"""Course topics, topic lessons and practice questions.

Students:    GET  /api/learn/{course}/topics                     the course's topics (planned on first use)
             GET  /api/learn/{course}/topics/{topic}/lesson      their personal lesson for one topic
             POST /api/practice/{course}/clos/{clo}              a new practice set (ungraded, AI)
             GET  /api/practice/sets/{id}  POST .../answer       answer one question, see why
Instructors: GET  /api/topics/course/{course}                    topics incl. hidden; PATCH /api/topics/{id};
             POST /api/topics/course/{course} (add), POST .../replan (fallback plans only)
             GET  /api/topics/course/{course}/student/{student}/lessons, GET /api/topics/lessons/{id}
             PUT  /api/topics/lessons/{id} (edit), POST /api/topics/lessons/{id}/rewrite (AI, with a note)
Topics and lessons are published without approval (product decision); every instructor change is audited.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..core.rbac import ensure_course_access, require
from ..db import get_db, utcnow
from ..models.content import StudentLesson
from ..models.outcomes import CLO, Course, Unit
from ..models.topics import PracticeSet, Topic
from ..models.user import User
from ..services import ai_gateway, paths, personal_lessons, practice, topic_planner

router = APIRouter(prefix="/api", tags=["topics"])


def _course(db: Session, user: User, course_id: int, write: bool = False) -> Course:
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "Course not found")
    ensure_course_access(db, user, course_id, write=write)
    return c


def _topic_out(db: Session, t: Topic, clos: dict[int, CLO], units: dict[int, Unit]) -> dict:
    u = units.get(t.unit_id)
    return {"id": t.id, "number": t.number, "title": t.title, "summary": t.summary, "status": t.status, "origin": t.origin,
            "unit_id": t.unit_id, "unit_number": u.number if u else None, "unit_title": u.title if u else None,
            "clos": [{"id": i, "code": clos[i].code, "description": clos[i].description} for i in t.clo_ids if i in clos],
            "edited_at": t.edited_at}


def _maps(db: Session, course_id: int):
    clos = {o.id: o for o in db.scalars(select(CLO).where(CLO.course_id == course_id))}
    units = {u.id: u for u in db.scalars(select(Unit).where(Unit.course_id == course_id))}
    return clos, units


# ---- students --------------------------------------------------------------------------------------------------

@router.get("/learn/{course_id}/topics")
def student_topics(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    c = _course(db, user, course_id)
    ts = topic_planner.ensure(db, user, c)
    db.commit()
    clos, units = _maps(db, course_id)
    p = paths.active_path(db, user.id, course_id)
    steps = {(s.meta or {}).get("topic_id"): s for s in paths.steps(db, p)} if p else {}
    have = set(db.scalars(select(StudentLesson.topic_id).where(StudentLesson.student_id == user.id, StudentLesson.course_id == course_id)))
    out = []
    for t in ts:
        d = _topic_out(db, t, clos, units)
        s = steps.get(t.id)
        d.update(in_path=bool(s), step_id=s.id if s else None, step_status=s.status if s else None, lesson_ready=t.id in have)
        out.append(d)
    return out


@router.get("/learn/{course_id}/topics/{topic_id}/lesson")
def topic_lesson(course_id: int, topic_id: int, refresh: bool = False, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    c = _course(db, user, course_id)
    t = db.get(Topic, topic_id)
    if not t or t.course_id != course_id or t.status != "published" or not t.clo_ids:
        raise HTTPException(404, "Topic not found")
    clo = db.get(CLO, t.clo_ids[0])
    res = personal_lessons.get_or_create(db, user, c, clo, refresh=refresh, topic=t)
    ts = topic_planner.topics(db, course_id)
    i = next((k for k, x in enumerate(ts) if x.id == t.id), 0)
    res["previous_topic"] = {"id": ts[i - 1].id, "title": ts[i - 1].title} if i > 0 else None
    res["next_topic"] = {"id": ts[i + 1].id, "title": ts[i + 1].title} if i + 1 < len(ts) else None
    res["topic_summary"] = t.summary
    db.commit()
    return res


class PracticeIn(BaseModel):
    topic_id: int | None = None


class AnswerIn(BaseModel):
    index: int
    choice: int


@router.post("/practice/{course_id}/clos/{clo_id}", status_code=201)
def new_practice(course_id: int, clo_id: int, body: PracticeIn, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    c = _course(db, user, course_id)
    clo = db.get(CLO, clo_id)
    if not clo or clo.course_id != course_id:
        raise HTTPException(404, "Outcome not found")
    topic = None
    if body.topic_id:
        topic = db.get(Topic, body.topic_id)
        if not topic or topic.course_id != course_id:
            raise HTTPException(404, "Topic not found")
    try:
        ps = practice.new_set(db, user, c, clo, topic)
    except practice.PracticeUnavailable:
        db.commit()  # keep the AI-call trace
        raise HTTPException(503, "Practice questions are not available right now (the AI is offline or busy). Please try again in a few minutes.")
    db.commit()
    return practice.out(ps)


@router.get("/practice/{course_id}/clos/{clo_id}/latest")
def latest_practice(course_id: int, clo_id: int, topic_id: int | None = None, db: Session = Depends(get_db),
                    user: User = Depends(require("learn"))):
    """The student's latest practice set for this topic (each lecture keeps its own), or null."""
    _course(db, user, course_id)
    clo = db.get(CLO, clo_id)
    if not clo or clo.course_id != course_id:
        raise HTTPException(404, "Outcome not found")
    ps = practice.latest(db, user.id, clo_id, topic_id)
    return practice.out(ps) if ps else None


def _own_set(db: Session, user: User, sid: int) -> PracticeSet:
    ps = db.get(PracticeSet, sid)
    if not ps or ps.student_id != user.id:
        raise HTTPException(404, "Practice set not found")
    return ps


@router.get("/practice/sets/{sid}")
def get_practice(sid: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    return practice.out(_own_set(db, user, sid))


@router.post("/practice/sets/{sid}/answer")
def answer_practice(sid: int, body: AnswerIn, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    ps = _own_set(db, user, sid)
    try:
        res = practice.answer(db, user, ps, body.index, body.choice)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return res


# ---- instructors -----------------------------------------------------------------------------------------------

class TopicPatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    clo_ids: list[int] | None = None
    status: str | None = None


class TopicIn(BaseModel):
    title: str
    summary: str = ""
    clo_ids: list[int]


@router.get("/topics/course/{course_id}")
def staff_topics(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("console:view", "reports:read"))):
    c = _course(db, user, course_id)
    if user.role == "instructor":
        topic_planner.ensure(db, user, c)
        db.commit()
    clos, units = _maps(db, course_id)
    ts = topic_planner.topics(db, course_id, include_hidden=True)
    used = set(db.scalars(select(StudentLesson.topic_id).where(StudentLesson.course_id == course_id, StudentLesson.topic_id.is_not(None))))
    return {"course": {"id": c.id, "code": c.code, "title": c.title, "credits": c.credits},
            "target": topic_planner.topic_count(c, len(clos)), "can_replan": not used and any(t.origin == "fallback" for t in ts),
            "topics": [_topic_out(db, t, clos, units) for t in ts]}


def _check_clos(db: Session, course_id: int, ids: list[int]) -> list[int]:
    valid = {o.id for o in db.scalars(select(CLO).where(CLO.course_id == course_id))}
    ids = [i for k, i in enumerate(ids) if i not in ids[:k]]
    if not ids or not set(ids) <= valid:
        raise HTTPException(422, "Pick at least one outcome of this course")
    return ids


@router.patch("/topics/{topic_id}")
def edit_topic(topic_id: int, body: TopicPatch, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    t = db.get(Topic, topic_id)
    if not t:
        raise HTTPException(404, "Topic not found")
    _course(db, user, t.course_id, write=True)
    changes = {}
    if body.title is not None:
        if len(body.title.strip()) < 3:
            raise HTTPException(422, "Give the topic a title")
        t.title, changes["title"] = body.title.strip()[:200], body.title.strip()[:200]
    if body.summary is not None:
        t.summary, changes["summary"] = body.summary.strip(), True
    if body.clo_ids is not None:
        t.clo_ids = _check_clos(db, t.course_id, body.clo_ids)
        t.unit_id = db.get(CLO, t.clo_ids[0]).unit_id
        changes["clo_ids"] = t.clo_ids
    if body.status is not None:
        if body.status not in ("published", "hidden"):
            raise HTTPException(422, "status must be published or hidden")
        t.status, changes["status"] = body.status, body.status
    t.edited_by, t.edited_at = user.id, utcnow()
    record(db, user, "topics.edited", "topic", t.id, changes, course_id=t.course_id)
    db.commit()
    clos, units = _maps(db, t.course_id)
    return _topic_out(db, t, clos, units)


@router.post("/topics/course/{course_id}", status_code=201)
def add_topic(course_id: int, body: TopicIn, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    _course(db, user, course_id, write=True)
    if len(body.title.strip()) < 3:
        raise HTTPException(422, "Give the topic a title")
    ids = _check_clos(db, course_id, body.clo_ids)
    last = max((t.number for t in topic_planner.topics(db, course_id, include_hidden=True)), default=0)
    t = Topic(course_id=course_id, unit_id=db.get(CLO, ids[0]).unit_id, number=last + 1, title=body.title.strip()[:200],
              summary=body.summary.strip(), clo_ids=ids, status="published", origin="instructor", edited_by=user.id, edited_at=utcnow())
    db.add(t)
    db.flush()
    record(db, user, "topics.added", "topic", t.id, {"title": t.title, "clo_ids": ids}, course_id=course_id)
    db.commit()
    clos, units = _maps(db, course_id)
    return _topic_out(db, t, clos, units)


@router.post("/topics/course/{course_id}/replan")
def replan(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    c = _course(db, user, course_id, write=True)
    try:
        topic_planner.replan(db, user, c)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except ai_gateway.AIUnavailable:
        db.commit()
        raise HTTPException(503, "The AI could not plan topics right now. Please try again later.")
    db.commit()
    return staff_topics(course_id, db, user)


def _lesson_out(db: Session, ls: StudentLesson, full: bool = False) -> dict:
    t = db.get(Topic, ls.topic_id) if ls.topic_id else None
    clo = db.get(CLO, ls.clo_id)
    d = {"id": ls.id, "student_id": ls.student_id, "topic_id": ls.topic_id, "topic_title": t.title if t else None,
         "topic_number": t.number if t else None, "clo_code": clo.code if clo else None, "level": ls.level,
         "level_label": personal_lessons.LEVEL_LABEL.get(ls.level, ls.level), "created_at": ls.created_at,
         "edited_at": ls.edited_at, "edited": bool(ls.edited_by), "instructor_note": ls.instructor_note}
    if full:
        d.update(lecture_md=ls.lecture_md, notes_md=ls.notes_md)
    return d


@router.get("/topics/course/{course_id}/student/{student_id}/lessons")
def student_lessons(course_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(require("console:view"))):
    _course(db, user, course_id)
    rows = db.scalars(select(StudentLesson).where(StudentLesson.course_id == course_id, StudentLesson.student_id == student_id)
                      .order_by(StudentLesson.id.desc())).all()
    latest: dict[tuple, StudentLesson] = {}
    for ls in rows:  # newest lesson per topic (or per CLO for CLO-level lessons)
        latest.setdefault((ls.topic_id, ls.clo_id if ls.topic_id is None else None), ls)
    return sorted((_lesson_out(db, ls) for ls in latest.values()), key=lambda d: (d["topic_number"] or 999, d["clo_code"] or ""))


def _staff_lesson(db: Session, user: User, lid: int, write: bool = False) -> StudentLesson:
    ls = db.get(StudentLesson, lid)
    if not ls:
        raise HTTPException(404, "Lesson not found")
    _course(db, user, ls.course_id, write=write)
    return ls


@router.get("/topics/lessons/{lid}")
def lesson_detail(lid: int, db: Session = Depends(get_db), user: User = Depends(require("console:view"))):
    return _lesson_out(db, _staff_lesson(db, user, lid), full=True)


class LessonEdit(BaseModel):
    lecture_md: str
    notes_md: str = ""


class RewriteIn(BaseModel):
    note: str


@router.put("/topics/lessons/{lid}")
def edit_lesson(lid: int, body: LessonEdit, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    ls = _staff_lesson(db, user, lid, write=True)
    if len(body.lecture_md.strip()) < 40:
        raise HTTPException(422, "The lesson is too short")
    ls.lecture_md, ls.notes_md, ls.edited_by, ls.edited_at = body.lecture_md.strip(), body.notes_md.strip(), user.id, utcnow()
    ls.slides = None  # the slides were made from the old text; the student now reads the edited article
    record(db, user, "lesson.edited_by_instructor", "student_lesson", ls.id, {"student_id": ls.student_id}, course_id=ls.course_id)
    db.commit()
    return _lesson_out(db, ls, full=True)


@router.post("/topics/lessons/{lid}/rewrite")
def rewrite_lesson(lid: int, body: RewriteIn, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    ls = _staff_lesson(db, user, lid, write=True)
    if len(body.note.strip()) < 5:
        raise HTTPException(422, "Tell the AI what to change (at least a few words)")
    student = db.get(User, ls.student_id)
    t = db.get(Topic, ls.topic_id) if ls.topic_id else None
    try:
        res = personal_lessons.get_or_create(db, student, db.get(Course, ls.course_id), db.get(CLO, ls.clo_id), topic=t,
                                             note=body.note, actor=user)
    except ai_gateway.AIUnavailable:
        db.commit()
        raise HTTPException(503, "The AI could not rewrite the lesson right now. Please try again later, or edit it by hand.")
    record(db, user, "lesson.rewritten_by_instructor", "student_lesson", res["id"], {"student_id": ls.student_id, "from": ls.id},
           course_id=ls.course_id)
    db.commit()
    return _lesson_out(db, db.get(StudentLesson, res["id"]), full=True)


# ---- suggested reading (approved teaching sources) for students -----------------------------------------------

@router.get("/learn/{course_id}/readings")
def readings(course_id: int, q: str | None = None, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """The course's approved suggested reading. With `q` (e.g. a topic title) also the passages that match it best,
    so a topic page can show the right pages of the reading."""
    from ..models.sources import Source
    from ..services import retrieval

    _course(db, user, course_id)
    srcs = db.scalars(select(Source).where(Source.course_id == course_id, Source.status == "approved").order_by(Source.title)).all()
    out = {"sources": [{"id": s.id, "title": s.title, "kind": s.kind, "filename": s.filename, "version": s.version_no,
                        "license_note": s.license_note} for s in srcs], "passages": []}
    if q and q.strip():
        out["passages"] = [{"source_id": h.source_id, "source_title": h.source_title, "heading": h.heading,
                            "text": h.text if len(h.text) <= 900 else h.text[:900].rsplit(" ", 1)[0] + " …"}
                           for h in retrieval.search(db, course_id, q.strip(), k=3)]
    return out


@router.get("/learn/{course_id}/readings/{sid}/file")
def reading_file(course_id: int, sid: int, db: Session = Depends(get_db), user: User = Depends(require("learn"))):
    """Download one approved reading of the student's course (the original file the instructor uploaded)."""
    from fastapi.responses import Response

    from ..core.audit import learning
    from ..core.crypto import read_encrypted_file
    from ..models.sources import Source

    _course(db, user, course_id)
    s = db.get(Source, sid)
    if not s or s.course_id != course_id or s.status != "approved":
        raise HTTPException(404, "Reading not found")
    learning(db, user, "reading_opened", "source", s.id, {}, course_id=course_id)
    db.commit()
    safe = "".join(ch for ch in s.filename if ch.isalnum() or ch in "._- ") or "reading"
    return Response(read_encrypted_file(s.file_ref), media_type=s.mime or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})
