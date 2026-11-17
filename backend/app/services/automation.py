"""Automation mode (settings.automation, on by default).

The app runs end to end without waiting for a teacher:

  PDF with CLOs/PLOs -> AI extraction -> curriculum approved (when the extraction has no errors; warnings are
  accepted with a recorded note) -> CLO-PLO mappings imported/suggested and approved -> course activated ->
  diagnostic / practice / reteach questions DRAFTED by the AI -> a teacher approves the questions (assessment
  questions always need a teacher's approval) -> the diagnostic goes live as soon as every CLO has enough approved
  questions -> students take the diagnostic -> personalised learning path -> personalised lectures and notes per
  student (services/personal_lessons.py).

Every automatic decision goes through the same functions a teacher's click uses (same checks, same audit trail,
same supersede rules) and is recorded with AUTO_NOTE. AI output stays traced and in the review queue, so teachers
can review, retire or replace anything afterwards. Anything the system cannot decide safely (for example
extraction errors, or CLOs it could not map) stops and is left for a person.
"""
from __future__ import annotations

import logging
import queue
import threading

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.audit import record
from ..db import utcnow

log = logging.getLogger(__name__)
AUTO_NOTE = "Approved automatically (automation is on) - teacher review is optional"
DIAGNOSTIC_EXTRA = 1  # generate a little above the minimum so the adaptive diagnostic has a choice
# Personal question sets need a bank with real choice at every difficulty: the diagnostic picks a different question
# of the same difficulty for each student, and quizzes never repeat a question for the same student.
DIAGNOSTIC_PER_LEVEL = 2   # x 3 difficulty levels per CLO
FORMATIVE_PER_CLO = 12  # multiple-choice formative questions (a quiz asks 5-7 of them)
WRITTEN_PER_CLO = 9     # formative true/false, fill in the blank and short answer (a quiz asks 3-5 of them)
RETEACH_PER_CLO = 6
TOP_UP = 10                # extra questions drafted when a student is running out of unseen ones
TOP_UP_CAP = 40


def enabled() -> bool:
    return bool(get_settings().automation)


def _reason(e: HTTPException) -> str:
    d = e.detail
    if isinstance(d, dict):
        parts = [str(d.get("message", ""))] + [str(x) for x in (d.get("blockers") or d.get("errors") or [])][:5]
        return " - ".join(p for p in parts if p)
    return str(d)


# ---- curriculum ------------------------------------------------------------------------------
def run_curriculum(db: Session, user, version_id: int, on_course=None) -> dict:
    """Review -> approve -> map -> activate, stopping (and saying why) at anything that needs a person."""
    from ..models.outcomes import Course
    from ..routers import outcomes as o

    out: dict = {"status": "uploaded", "courses": [], "stopped": None}
    try:
        o.start_review(version_id, db, user)
    except HTTPException as e:
        out["stopped"] = f"Review could not start: {_reason(e)}"
        return out
    out["status"] = "in_review"
    errors = [i["message"] for i in o.issues_with_acks(db, version_id) if i["level"] == "error"]
    if errors:
        out["stopped"] = "The AI extraction found problems a person needs to fix in Outcome review."
        out["errors"] = errors[:10]
        return out
    if any(i["level"] == "warning" and not i["acknowledged"] for i in o.issues_with_acks(db, version_id)):
        try:
            o.acknowledge_all(version_id, o.AckAllIn(note=AUTO_NOTE), db, user)
        except HTTPException as e:
            db.rollback()
            out["stopped"] = f"The warnings could not be accepted automatically: {_reason(e)}. Accept them in Outcome review."
            return out
    try:
        o.approve_version(version_id, db, user)
    except HTTPException as e:
        out["stopped"] = f"Approval was blocked: {_reason(e)}"
        return out
    out["status"] = "approved"
    courses = db.scalars(select(Course).where(Course.curriculum_version_id == version_id).order_by(Course.code)).all()
    for i, c in enumerate(courses, 1):
        if on_course is not None:
            on_course(i, len(courses), c.code)  # progress display only
        out["courses"].append(activate_course(db, user, c))
    if out["courses"] and all(c["status"] == "active" for c in out["courses"]):
        out["status"] = "active"
    return out


def continue_after_approval(version_id: int, user_id: int) -> None:
    """After a person fixed what automation could not decide and approved the review: link, activate and draft
    questions for the version's courses (in the background; inline under tests)."""
    def run():
        from ..db import SessionLocal
        from ..models.outcomes import Course
        from ..models.user import User

        db = SessionLocal()
        try:
            user = db.get(User, user_id)
            for c in db.scalars(select(Course).where(Course.curriculum_version_id == version_id,
                                                     Course.status == "approved").order_by(Course.code)).all():
                if activate_course(db, user, c)["status"] == "active":
                    prepare_course_async(c.id, user_id)
        except Exception:  # never crash the server; the courses can still be activated from the Mappings page
            log.exception("Continuing automation for curriculum version %s failed", version_id)
            db.rollback()
        finally:
            db.close()

    if get_settings().app_env == "test":
        run()
    else:
        threading.Thread(target=run, daemon=True, name=f"after-approval-{version_id}").start()


def activate_course(db: Session, user, course) -> dict:
    """Import stated mappings, ask the AI for the rest, approve them, and activate the course."""
    from ..routers import mappings as m

    res = {"id": course.id, "code": course.code, "status": course.status}
    def approve_suggested():
        for mp in m.coverage(db, course)["mappings"]:
            if mp["status"] == "suggested":
                m.decide(mp["id"], m.DecideIn(decision="approve", note=AUTO_NOTE), db, user)

    try:
        # The document's own CLO-PLO links come first and are approved; only CLOs the document leaves unlinked go
        # to the AI. (Imported links start as "suggested", so checking for unlinked CLOs before approving them sent
        # every course to the AI - one slow call per course, plus links that are not in the document.)
        m.import_document(course.id, db, user)
        approve_suggested()
        if m.coverage(db, course)["unmapped_clos"]:
            m.suggest(course.id, db, user)
            approve_suggested()
        m.activate(course.id, db, user)
        res["status"] = "active"
    except HTTPException as e:
        res["status"] = course.status
        res["reason"] = _reason(e)
    return res


# ---- questions and diagnostic ------------------------------------------------------------------
# ONE background worker prepares courses one after another. (A thread per course made ~19 writers fight over the
# single SQLite file -> "database is locked" - and fired that many Gemini calls at once, beyond the rate limit.)
_queued: set[int] = set()  # waiting or being prepared
_extra: dict[tuple[int, str], int] = {}  # (clo_id, purpose) -> extra questions requested on top of the base target
_extra_pending: set[tuple[int, str]] = set()
_lock = threading.Lock()
_jobs: "queue.Queue[tuple[int, int | None]]" = queue.Queue()
_worker: threading.Thread | None = None


def is_preparing(course_id: int) -> bool:
    with _lock:
        return course_id in _queued


def prepare_course_async(course_id: int, user_id: int | None) -> bool:
    """Queue a course for question drafting (runs inline under tests). False if it is already queued."""
    global _worker
    with _lock:
        if course_id in _queued:
            return False
        _queued.add(course_id)
    if get_settings().app_env == "test":
        _prepare_guarded(course_id, user_id)
        return True
    _jobs.put((course_id, user_id))
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True, name="course-preparation")
            _worker.start()
    return True


def _work():
    while True:
        course_id, user_id = _jobs.get()
        try:
            _prepare_guarded(course_id, user_id)
        finally:
            _jobs.task_done()


def _prepare_guarded(course_id: int, user_id: int | None):
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        prepare_course(db, course_id, user_id)
    except Exception:  # never let background work crash the server; the next visit / restart retries
        log.exception("Preparing course %s failed", course_id)
        db.rollback()
    finally:
        db.close()
        with _lock:
            _queued.discard(course_id)


def resume_pending() -> int:
    """On start-up: queue active courses whose questions were never fully drafted (e.g. the server stopped)."""
    from ..db import SessionLocal
    from ..models.items import DiagnosticConfig
    from ..models.outcomes import Course

    if not enabled() or get_settings().app_env == "test":
        return 0
    db = SessionLocal()
    try:
        ids = [c.id for c in db.scalars(select(Course).where(Course.status == "active")).all()
               if not (cfg := db.get(DiagnosticConfig, c.id)) or not cfg.published]
    finally:
        db.close()
    for cid in ids:
        prepare_course_async(cid, None)
    return len(ids)


def prepare_course(db: Session, course_id: int, user_id: int | None) -> dict:
    """Draft diagnostic / formative / reteach items for every CLO (pending a teacher's approval), then publish the
    diagnostic if enough approved items already exist.

    Optimised: ONE AI call per CLO asks for every question type it still needs (instead of one per type), the
    course outline is built once and sent first in each prompt (shared prefix), and several CLOs run at once
    (call_json_many, within the rate limit). A CLO whose batched call fails falls back to the per-type path,
    which also covers offline generation from teaching sources."""
    from ..models.items import Item
    from ..models.outcomes import CLO, Course
    from ..models.user import User
    from ..routers import items as r
    from . import items as gen

    course = db.get(Course, course_id)
    user = db.get(User, user_id) if user_id else None
    if not course or course.status not in ("approved", "active"):
        return {"published": False, "problems": ["Course is not approved"]}
    cfg = r.get_config(db, course_id)
    targets = {"diagnostic": max(cfg.min_items_per_clo + DIAGNOSTIC_EXTRA, 3 * DIAGNOSTIC_PER_LEVEL),
               "formative": FORMATIVE_PER_CLO, "reteach": RETEACH_PER_CLO, "written": WRITTEN_PER_CLO}
    db.commit()  # release the write lock before the first (slow) AI call
    from . import ai_gateway, course_context

    problems, created = [], 0
    clos = db.scalars(select(CLO).where(CLO.course_id == course_id).order_by(CLO.code)).all()

    def missing(clo) -> dict[str, int]:
        need = {}
        for purpose, target in targets.items():
            q = select(func.count(Item.id)).where(Item.clo_id == clo.id, Item.status.in_(["approved", "pending"]))
            if purpose == "written":  # written questions are formative items with a written type
                q = q.where(Item.purpose == "formative", Item.qtype.is_not(None), Item.qtype != "mcq")
            else:
                q = q.where(Item.purpose == purpose, (Item.qtype.is_(None)) | (Item.qtype == "mcq"))
            have = db.scalar(q) or 0
            need[purpose] = max(0, target + _extra.get((clo.id, purpose), 0) - have)
        return need

    def save(clo, purpose, items, origin, call_id):
        nonlocal created
        for data in items:
            try:
                r._new_item(db, user, course, clo, "formative" if purpose == "written" else purpose, data, origin, call_id)
                created += 1
            except HTTPException:
                pass  # an invalid item is skipped; the next run tops up

    needs = {clo.id: missing(clo) for clo in clos}
    todo = [clo for clo in clos if any(needs[clo.id].values())]
    if todo:
        outline = course_context.outline(db, course)  # whole course, built once, identical in every prompt
        jobs = [gen.bundle_job(db, course, clo, needs[clo.id], outline) for clo in todo]
        db.commit()  # no write transaction may stay open during the AI calls
        results = ai_gateway.call_json_many(db, user, jobs)
        call_ids = db.info.get("last_ai_call_ids") or [None] * len(jobs)
        for clo, job, res, call_id in zip(todo, jobs, results, call_ids):
            if isinstance(res, Exception):
                continue  # handled by the per-type fallback below
            for purpose, items in gen.bundle_items(res, needs[clo.id], job["chunk_ids"]).items():
                save(clo, purpose, items, "ai", call_id)
        db.commit()
    for clo in clos:  # top up anything the batched calls did not cover (one call per missing type)
        for purpose, n in missing(clo).items():
            if n <= 0:
                continue
            try:
                if purpose == "written":
                    items, origin, call_id = gen.generate_written_for_clo(db, user, course, clo, n)
                else:
                    items, origin, call_id = gen.generate_for_clo(db, user, course, clo, n, purpose)
            except ValueError as e:
                problems.append(str(e))
                db.commit()  # keep the AI trace; never carry an open write transaction into the next AI call
                continue
            save(clo, purpose, items, origin, call_id)
            db.commit()
    with _lock:
        _extra_pending.difference_update({k for k in _extra_pending if k[0] in {c.id for c in clos}})
    published = publish_if_ready(db, course_id, user)
    record(db, user, "automation.course_prepared", "course", course_id,
           {"items_created": created, "published": published, "problems": problems[:5]}, course_id=course_id)
    db.commit()
    return {"published": published, "items_created": created, "problems": problems}


def request_more_items(course_id: int, clo_id: int, purpose: str) -> bool:
    """A student is running out of unseen questions for a CLO: draft TOP_UP more (pending a teacher's approval).
    Asked once per CLO and purpose until that drafting has run; capped at TOP_UP_CAP extra."""
    if not enabled():
        return False
    key = (clo_id, purpose)
    with _lock:
        if key in _extra_pending or _extra.get(key, 0) >= TOP_UP_CAP:
            return False
        _extra[key] = _extra.get(key, 0) + TOP_UP
        _extra_pending.add(key)
    prepare_course_async(course_id, None)
    return True


def publish_if_ready(db: Session, course_id: int, user=None) -> bool:
    from ..routers import items as r

    cfg = r.get_config(db, course_id)
    if cfg.published:
        return True
    if not r.coverage(db, course_id)["complete"]:
        return False
    cfg.published, cfg.published_at, cfg.published_by = True, utcnow(), getattr(user, "id", None)
    record(db, user, "diagnostic.published", "course", course_id, {"automatic": True, "note": AUTO_NOTE}, course_id=course_id)
    db.commit()
    return True
