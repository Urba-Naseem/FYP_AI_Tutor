"""Course topics: a full semester of teaching topics drawn from the approved CLOs (and the PLOs they map to).

* How many: about 4 topics per credit hour (a 3-credit course -> 12), 12 when credits are unknown, never fewer than
  the number of CLOs, at most 20.
* The AI drafts the plan (titles, summaries, CLO links, basic -> advanced) from the course outline. Every CLO must be
  covered and only the course's own CLO codes may be used; otherwise - or when the AI is unavailable - a plain plan is
  built from the CLOs themselves (origin "fallback"), so students are never blocked.
* Topics are published at once (product decision); instructors can edit, hide or add topics, and can re-plan a
  fallback plan with the AI while no student has a lesson for it yet.
* Planned lazily - the first time a path is built or topics are listed for the course - so activating many courses
  does not spend AI quota on courses nobody studies yet.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..models.content import StudentLesson
from ..models.outcomes import CLO, Course, Unit
from ..models.topics import Topic
from . import ai_gateway, course_context

PER_CREDIT = 4
DEFAULT_TOPICS = 12
MAX_TOPICS = 20
SYSTEM = (
    "You are a university curriculum designer. Plan the weekly teaching topics of ONE course for a full semester. "
    "Order topics from foundational to advanced, follow the unit order, keep each topic focused, and make the plan "
    "comprehensive: together the topics must teach every course learning outcome (CLO) completely at its Bloom level. "
    "Use only the CLO codes given; never invent outcomes."
)


def topic_count(course: Course, n_clos: int) -> int:
    base = (course.credits * PER_CREDIT) if course.credits else DEFAULT_TOPICS
    return max(n_clos, min(MAX_TOPICS, base))


def _clos(db: Session, course: Course) -> list[CLO]:
    units = {u.id: u.number for u in db.scalars(select(Unit).where(Unit.course_id == course.id))}
    clos = db.scalars(select(CLO).where(CLO.course_id == course.id)).all()
    return sorted(clos, key=lambda c: (units.get(c.unit_id, 999), c.code))


def fallback_plan(clos: list[CLO], n: int) -> list[dict]:
    """Split n topics over the CLOs in teaching order (each CLO at least one; earlier CLOs get the extra ones)."""
    if not clos:
        return []
    per = [n // len(clos)] * len(clos)
    for i in range(n - sum(per)):
        per[i % len(clos)] += 1
    out = []
    for clo, k in zip(clos, per):
        short = clo.description.rstrip(".")
        for part in range(1, k + 1):
            stage = ("Foundations" if part == 1 else "Applying the ideas" if part < k else "Bringing it together") if k > 1 else "Core ideas"
            out.append({"title": f"{clo.code} · {stage}" + (f" ({part}/{k})" if k > 2 else ""),
                        "summary": f"{stage} for: {short}.", "clos": [clo.code]})
    return out


def _valid(plan: list[dict], clos: list[CLO], n: int) -> bool:
    codes = {c.code for c in clos}
    if not plan or not (max(len(clos), n - 4) <= len(plan) <= n + 4):
        return False
    covered = set()
    for t in plan:
        if not str(t.get("title") or "").strip() or not t.get("clos") or not set(t["clos"]) <= codes:
            return False
        covered |= set(t["clos"])
    return covered == codes


def _ai_plan(db: Session, actor, course: Course, clos: list[CLO], n: int) -> list[dict] | None:
    from .schemas import TopicPlanOut

    prompt = (f"{course_context.outline(db, course)}\n\nPlan exactly {n} teaching topics for this course "
              f"(about one per week). For each topic give a title, a 2-3 sentence summary, and the CLO codes it serves "
              f"(main CLO first). Every CLO ({', '.join(c.code for c in clos)}) must be served by at least one topic; "
              "CLOs that need more teaching get more topics.")
    try:
        data = ai_gateway.as_object(ai_gateway.call_json(db, actor, "course.topics", prompt, SYSTEM, temperature=0.3,
                                                         course_id=course.id, schema=TopicPlanOut, max_output_tokens=8192))
    except ai_gateway.AIUnavailable:
        return None
    plan = [{"title": str(t.get("title") or "").strip()[:200], "summary": str(t.get("summary") or "").strip(),
             "clos": [str(c).strip().upper() for c in (t.get("clos") or [])]} for t in (data.get("topics") or [])]
    # keep only real codes in each topic, preserving order
    codes = {c.code for c in clos}
    for t in plan:
        t["clos"] = [c for i, c in enumerate(t["clos"]) if c in codes and c not in t["clos"][:i]]
    return plan if _valid(plan, clos, n) else None


def _save(db: Session, course: Course, clos: list[CLO], plan: list[dict], origin: str) -> list[Topic]:
    by_code = {c.code: c for c in clos}
    order = {c.code: i for i, c in enumerate(clos)}
    # teaching order: by the main CLO's position (unit order), keeping the plan's order inside a CLO
    plan = sorted(enumerate(plan), key=lambda it: (order[it[1]["clos"][0]], it[0]))
    out = []
    for i, (_, t) in enumerate(plan, start=1):
        main = by_code[t["clos"][0]]
        tp = Topic(course_id=course.id, unit_id=main.unit_id, number=i, title=t["title"], summary=t["summary"],
                   clo_ids=[by_code[c].id for c in t["clos"]], status="published", origin=origin)
        db.add(tp)
        out.append(tp)
    db.flush()
    return out


def topics(db: Session, course_id: int, include_hidden: bool = False) -> list[Topic]:
    q = select(Topic).where(Topic.course_id == course_id).order_by(Topic.number)
    if not include_hidden:
        q = q.where(Topic.status == "published")
    return list(db.scalars(q))


def ensure(db: Session, actor, course: Course, use_ai: bool = True) -> list[Topic]:
    """Topics of the course, planning them first if the course has none (and approved CLOs)."""
    have = topics(db, course.id, include_hidden=True)
    if have:
        return [t for t in have if t.status == "published"]
    clos = _clos(db, course)
    if not clos:
        return []
    n = topic_count(course, len(clos))
    plan = _ai_plan(db, actor, course, clos, n) if use_ai else None
    origin = "ai" if plan else "fallback"
    saved = _save(db, course, clos, plan or fallback_plan(clos, n), origin)
    record(db, actor, "topics.planned", "course", course.id, {"topics": len(saved), "origin": origin}, course_id=course.id)
    return saved


def replan(db: Session, actor, course: Course) -> list[Topic]:
    """Re-plan with the AI - only while no student has a lesson for the current topics."""
    have = topics(db, course.id, include_hidden=True)
    used = db.scalar(select(func.count()).select_from(StudentLesson).where(StudentLesson.topic_id.in_([t.id for t in have] or [0])))
    if used:
        raise ValueError("Students already have lessons for these topics; edit the topics instead")
    clos = _clos(db, course)
    plan = _ai_plan(db, actor, course, clos, topic_count(course, len(clos)))
    if not plan:
        raise ai_gateway.AIUnavailable("The AI could not plan topics right now")
    for t in have:
        db.delete(t)
    db.flush()
    saved = _save(db, course, clos, plan, "ai")
    record(db, actor, "topics.replanned", "course", course.id, {"topics": len(saved)}, course_id=course.id)
    return saved


def by_clo(ts: list[Topic]) -> dict[int, list[Topic]]:
    """Topics grouped under the CLO they mainly serve, in teaching order."""
    out: dict[int, list[Topic]] = {}
    for t in ts:
        if t.clo_ids:
            out.setdefault(t.clo_ids[0], []).append(t)
    return out
