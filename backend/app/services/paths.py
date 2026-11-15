"""US-4.3 - Generate and explain the learning path.

Rules (path-rules/v2), applied to every CLO so the path always covers all CLOs:
  ORDER   Units follow the curriculum order (unit 1, 2, ...) because later units
          build on earlier ones. Inside a unit the student's WEAKEST CLO comes first (lowest diagnostic score), so
          two students with different results get a different order; without a diagnostic, code order.
  R-READY      readiness "ready"       -> review (short check quiz to confirm mastery); its topics stay optional
  R-DEVELOPING readiness "developing"  -> learn (one step per course topic of the CLO), practice
  R-NOTREADY   readiness "not_ready"   -> remedial (foundations), learn (every topic), practice
  R-NODATA     no diagnostic result    -> learn (every topic), practice
A CLO without topics keeps one "learn" step. Topics come from topic_planner (about 4 per credit hour).
Readiness comes from the student's diagnostic or, for a semester 2+ course with prerequisites and no diagnostic, from
the final result in the prerequisite course(s) (diagnostic.course_readiness); the same rules apply to both.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning, record
from ..models.outcomes import CLO, Unit
from ..models.paths import LearningPath, PathStep
from . import criteria as crit
from . import topic_planner
from .diagnostic import course_readiness

RULES_VERSION = "path-rules/v2"
STEP_LABEL = {"remedial": "Foundations", "learn": "Study", "practice": "Formative quiz", "review": "Quick check", "reteach": "Reteach"}


def active_path(db: Session, student_id: int, course_id: int) -> LearningPath | None:
    return db.scalar(select(LearningPath).where(LearningPath.student_id == student_id, LearningPath.course_id == course_id,
                                                LearningPath.status == "active"))


def steps(db: Session, path: LearningPath) -> list[PathStep]:
    return db.scalars(select(PathStep).where(PathStep.path_id == path.id).order_by(PathStep.position)).all()


def plan(db: Session, student_id: int, course_id: int) -> tuple[list[dict], str, str]:
    """Pure rule application. Returns (step dicts, summary, trigger)."""
    ready, info = course_readiness(db, student_id, course_id)
    from_prereq = info["source"] == "prerequisite"

    def result(code: str, pct: str, label: str) -> str:
        if from_prereq:
            return f"Your final result in the prerequisite {info['names']} was {pct} ({label}), which sets your starting point for {code}"
        return f"Your diagnostic score for {code} was {pct} ({label})"
    units = db.scalars(select(Unit).where(Unit.course_id == course_id).order_by(Unit.number)).all()
    topics_of = topic_planner.by_clo(topic_planner.topics(db, course_id))

    def learn_steps(u, clo, rule: str, first_reason: str) -> list[dict]:
        ts = topics_of.get(clo.id, [])
        if not ts:
            return [dict(unit_id=u.id, clo_id=clo.id, step_type="learn", rule=rule, rationale=first_reason)]
        return [dict(unit_id=u.id, clo_id=clo.id, step_type="learn", rule=rule,
                     rationale=(first_reason if i == 0 else f"Topic {i + 1} of {len(ts)} for {clo.code}: builds on the previous topic."),
                     meta={"topic_id": t.id, "topic_title": t.title, "topic_number": t.number})
                for i, t in enumerate(ts)]

    out: list[dict] = []
    counts = {"ready": 0, "developing": 0, "not_ready": 0, "none": 0}
    for u in units:
        unit_clos = db.scalars(select(CLO).where(CLO.unit_id == u.id).order_by(CLO.code)).all()
        if ready:  # weakest first, so the order follows this student's own diagnostic
            unit_clos = sorted(unit_clos, key=lambda c: (ready[c.id].score_pct if c.id in ready else 101, c.code))
        weakest = unit_clos[0] if ready and len(unit_clos) > 1 and unit_clos[0].id in ready else None
        for clo in unit_clos:
            thr = crit.resolve(db, clo)["threshold"]
            r = ready.get(clo.id)
            level = r.level if r else "none"
            counts[level] += 1
            pct = f"{r.score_pct:.0f}%" if r else None
            base = f"Unit {u.number} comes {'first' if u.number == units[0].number else 'after the earlier units it builds on'}. " + (
                f"{clo.code} is your weakest outcome in this unit, so it comes first. " if weakest is not None and clo.id == weakest.id else "")
            n_topics = len(topics_of.get(clo.id, []))
            topics_txt = f" ({n_topics} topic{'s' if n_topics != 1 else ''})" if n_topics else ""
            if level == "ready":
                out.append(dict(unit_id=u.id, clo_id=clo.id, step_type="review", rule="R-READY",
                                rationale=base + f"{result(clo.code, pct, 'ready')}, so you only need a short check to confirm you reach the {thr:.0f}% attainment level."
                                + (f" Its {n_topics} topics stay open if you want to revise." if n_topics else "")))
            elif level == "developing":
                out += learn_steps(u, clo, "R-DEVELOPING", base + f"{result(clo.code, pct, 'developing')}: study the topics{topics_txt} to close the gaps.")
                out += [dict(unit_id=u.id, clo_id=clo.id, step_type="practice", rule="R-DEVELOPING",
                             rationale=f"Take the formative quiz for {clo.code}; you need {thr:.0f}% to attain it (practice questions are there to warm up first).")]
            elif level == "not_ready":
                out.append(dict(unit_id=u.id, clo_id=clo.id, step_type="remedial", rule="R-NOTREADY",
                                rationale=base + f"{result(clo.code, pct, 'not ready yet')}, so you start with a foundations explanation before the main material."))
                out += learn_steps(u, clo, "R-NOTREADY", f"With the foundations in place, study every topic{topics_txt} for {clo.code}.")
                out += [dict(unit_id=u.id, clo_id=clo.id, step_type="practice", rule="R-NOTREADY",
                             rationale=f"Take the formative quiz for {clo.code}; you need {thr:.0f}% to attain it (practice questions are there to warm up first).")]
            else:
                out += learn_steps(u, clo, "R-NODATA", base + f"There is no diagnostic result for {clo.code}, so you study all its topics{topics_txt}.")
                out += [dict(unit_id=u.id, clo_id=clo.id, step_type="practice", rule="R-NODATA",
                             rationale=f"Take the formative quiz for {clo.code}; you need {thr:.0f}% to attain it (practice questions are there to warm up first).")]
    trigger = "prerequisite" if from_prereq else "diagnostic" if ready else "no_diagnostic"
    origin = (f"From your final result in the prerequisite {info['names']} ({info['score_pct']:.0f}%, no new diagnostic needed)"
              if from_prereq else "From your diagnostic")
    n_learn = sum(1 for d in out if d["step_type"] == "learn" and d.get("meta", {}).get("topic_id"))
    summary = (f"Your path covers all {sum(counts.values())} course outcomes in unit order"
               + (f" through {n_learn} topics picked for you" if n_learn else "") + ", weakest outcome first in each unit. "
               + (f"{origin}: {counts['ready']} ready (quick check only), {counts['developing']} developing "
                  f"(study + practice) and {counts['not_ready']} not ready yet (foundations first)."
                  if ready else "You have not taken the diagnostic, so every outcome is studied fully.")
               + f" Rules: {RULES_VERSION}.")
    return out, summary, trigger


def save_version(db: Session, actor, student_id: int, course_id: int, step_dicts: list[dict], summary: str, trigger: str,
                 reason: str | None = None, keep_lock: bool = True, restored_from: int | None = None) -> LearningPath:
    old = active_path(db, student_id, course_id)
    last = db.scalar(select(LearningPath.version_no).where(LearningPath.student_id == student_id, LearningPath.course_id == course_id)
                     .order_by(LearningPath.version_no.desc()).limit(1)) or 0
    p = LearningPath(student_id=student_id, course_id=course_id, version_no=last + 1, status="active", trigger=trigger,
                     reason=reason, summary=summary, created_by=getattr(actor, "id", None), restored_from=restored_from)
    if old:
        old.status = "superseded"
        if keep_lock:
            p.locked, p.lock_reason, p.locked_by = old.locked, old.lock_reason, old.locked_by
    db.add(p)
    db.flush()
    for i, s in enumerate(step_dicts):
        db.add(PathStep(path_id=p.id, position=i + 1, unit_id=s["unit_id"], clo_id=s["clo_id"], step_type=s["step_type"],
                        status=s.get("status", "pending"), rationale=s["rationale"], rule=s["rule"],
                        completed_at=s.get("completed_at"), meta=s.get("meta", {})))
    db.flush()
    return p


def generate_path(db: Session, student, course_id: int, trigger: str = "diagnostic") -> LearningPath | None:
    current = active_path(db, student.id, course_id)
    if current and current.locked:
        record(db, student, "path.generation_skipped", "learning_path", current.id, {"reason": "path locked by instructor"},
               course_id=course_id)
        return current
    from ..models.outcomes import Course

    course = db.get(Course, course_id)
    if course:
        topic_planner.ensure(db, student, course)  # plan the course topics the first time anyone needs them
    step_dicts, summary, trig = plan(db, student.id, course_id)
    p = save_version(db, student, student.id, course_id, step_dicts, summary, trig)
    learning(db, student, "path_generated", "learning_path", p.id, {"version": p.version_no, "steps": len(step_dicts), "rules": RULES_VERSION},
             course_id=course_id)
    return p


def step_dict(s: PathStep) -> dict:
    return {"unit_id": s.unit_id, "clo_id": s.clo_id, "step_type": s.step_type, "status": s.status, "rationale": s.rationale,
            "rule": s.rule, "completed_at": s.completed_at, "meta": dict(s.meta or {})}


def upgrade_to_topics(db: Session, student, course_id: int) -> LearningPath | None:
    """Give a path built before the course had topics its topic steps (a new version). Progress is kept: a CLO whose
    study step was done has all its topic steps done; quiz steps keep their status. Locked paths, paths in a reteach
    loop and paths that already have topic steps are left alone."""
    p = active_path(db, student.id, course_id)
    if not p or p.locked or not topic_planner.topics(db, course_id):
        return p
    old = steps(db, p)
    if any((s.meta or {}).get("topic_id") for s in old) or any(s.step_type == "reteach" for s in old):
        return p
    new, summary, trig = plan(db, student.id, course_id)
    done = {(s.clo_id, s.step_type): s for s in old if s.status in ("done", "skipped")}
    for d in new:
        prev = done.get((d["clo_id"], d["step_type"]))
        if prev:
            d["status"], d["completed_at"] = prev.status, prev.completed_at
    q = save_version(db, student, student.id, course_id, new, summary, trig, reason="Course topics added")
    learning(db, student, "path_upgraded_to_topics", "learning_path", q.id, {"version": q.version_no, "steps": len(new)},
             course_id=course_id)
    return q


QUIZ_KINDS = ("practice", "review")


def walk_to_quiz(db: Session, student_id: int, course_id: int, clo_id: int) -> None:
    """Mark every pending step before the CLO's first quiz step as done (demo data and tests only - a real student
    does these steps one by one in the app)."""
    p = active_path(db, student_id, course_id)
    if not p:
        return
    sts = steps(db, p)
    target = next((s for s in sts if s.clo_id == clo_id and s.step_type in QUIZ_KINDS and s.status == "pending"), None)
    if not target:
        return
    from ..db import utcnow

    for s in sts:
        if s.position < target.position and s.status == "pending":
            s.status, s.completed_at = "done", utcnow()
    db.flush()


def next_step(db: Session, path: LearningPath) -> PathStep | None:
    return next((s for s in steps(db, path) if s.status == "pending"), None)


def gate(db: Session, student_id: int, course_id: int, clo_id: int, kinds: tuple[str, ...] = QUIZ_KINDS) -> str | None:
    """Step-by-step rule: a quiz for an outcome opens only when one of its quiz steps is the student's current step
    (or already done - retakes are fine). Reading course material is never blocked. Returns why it is blocked, or
    None. Without a path every quiz is closed; outcomes without such a step in the path are not restricted."""
    p = active_path(db, student_id, course_id)
    if not p:
        # no path yet (new course): quizzes open in the order of the path, so the path must exist first
        return ("Start your learning path first: take the diagnostic test (or start your path without it) - "
                "quizzes then open one by one in the order of your path.")
    sts = steps(db, p)
    mine = [s for s in sts if s.clo_id == clo_id and s.step_type in kinds]
    if not mine:
        return None
    nxt = next((s for s in sts if s.status == "pending"), None)
    if nxt is None or any(s.status in ("done", "skipped") or s.position <= nxt.position for s in mine):
        return None
    label = (nxt.meta or {}).get("topic_title") or STEP_LABEL.get(nxt.step_type, nxt.step_type)
    from ..models.outcomes import CLO

    code = db.get(CLO, nxt.clo_id).code
    return f"Finish your path in order first: your next step is \"{label}\" ({code})."
