"""US-4.2 - Generate and approve diagnostic items; enforce minimum approved coverage per CLO.

The same item bank also serves formative quizzes (US-6.1) and reteach work (US-6.3).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..core.rbac import accessible_course_ids, ensure_course_access, require
from ..db import get_db, utcnow
from ..models.items import PURPOSES, DiagnosticConfig, Item
from ..models.outcomes import CLO, Course
from ..models.user import User
from ..services import ai_gateway, automation
from ..services import items as gen

router = APIRouter(prefix="/api", tags=["items"])


class GenerateIn(BaseModel):
    course_id: int
    purpose: str = "diagnostic"
    clo_id: int | None = None  # None = every CLO of the course
    count: int = Field(3, ge=1, le=10)


class ItemIn(BaseModel):
    course_id: int
    clo_id: int
    purpose: str
    stem: str
    options: list[str]
    answer_index: int
    explanation: str = ""
    hints: list[str] = []
    difficulty: int = Field(2, ge=1, le=3)


class ItemPatch(BaseModel):
    stem: str | None = None
    options: list[str] | None = None
    answer_index: int | None = None
    explanation: str | None = None
    hints: list[str] | None = None
    difficulty: int | None = Field(None, ge=1, le=3)


class ReviewIn(BaseModel):
    decision: str
    note: str = ""


class ConfigIn(BaseModel):
    min_items_per_clo: int = Field(3, ge=1, le=20)
    items_per_clo: int = Field(3, ge=1, le=10)


def item_out(db: Session, it: Item, with_key: bool = True) -> dict:
    clo = db.get(CLO, it.clo_id)
    d = {"id": it.id, "course_id": it.course_id, "clo_id": it.clo_id, "clo_code": clo.code if clo else None,
         "purpose": it.purpose, "stem": it.stem, "options": it.options, "difficulty": it.difficulty, "status": it.status,
         "origin": it.origin, "version_no": it.version_no, "supersedes_id": it.supersedes_id, "created_at": it.created_at,
         "review_note": it.review_note, "source_chunk_ids": it.source_chunk_ids, "ai_call_id": it.ai_call_id}
    if with_key:
        d.update(answer_index=it.answer, explanation=it.explanation, hints=it.hints)
    return d


def _course(db, user, cid, write=False) -> Course:
    c = db.get(Course, cid)
    if not c:
        raise HTTPException(404, "Course not found")
    ensure_course_access(db, user, cid, write=write)
    if c.status not in ("approved", "active"):
        raise HTTPException(409, "Course outcomes must be approved first")
    return c


def _new_item(db, user, course, clo, purpose, data, origin, ai_call_id=None, supersedes=None, version=1) -> Item:
    v = gen.validate(data)
    if not v:
        raise HTTPException(422, "Item needs a stem, exactly 4 distinct options and a valid answer index")
    # Assessment questions ALWAYS need a teacher's approval, also in automation mode (the AI only drafts them).
    it = Item(course_id=course.id, clo_id=clo.id, purpose=purpose, stem=v["stem"], options=v["options"],
              answer_index=str(v["answer_index"]), explanation=v["explanation"], hints=v["hints"], difficulty=v["difficulty"],
              status="pending", origin=origin, version_no=version, supersedes_id=supersedes,
              source_chunk_ids=data.get("chunk_ids", []), ai_call_id=ai_call_id, created_by=getattr(user, "id", None))
    db.add(it)
    db.flush()
    ai_gateway.route_review(db, f"{purpose}_item" if purpose != "reteach" else "formative_item", "item", it.id, course.id,
                            f"{course.code} {clo.code} {purpose} item v{version} ({origin})", user, ai_call_id)
    return it


@router.post("/items/generate")
def generate(body: GenerateIn, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    if body.purpose not in PURPOSES:
        raise HTTPException(422, f"purpose must be one of {PURPOSES}")
    course = _course(db, user, body.course_id, write=True)
    clos = [db.get(CLO, body.clo_id)] if body.clo_id else db.scalars(select(CLO).where(CLO.course_id == course.id).order_by(CLO.code)).all()
    if not clos or any(c is None or c.course_id != course.id for c in clos):
        raise HTTPException(404, "CLO not found in this course")
    created, problems, origins = [], [], set()
    for clo in clos:
        try:
            items, origin, call_id = gen.generate_for_clo(db, user, course, clo, body.count, body.purpose)
        except ValueError as e:
            problems.append(str(e))
            db.commit()  # never hold the database write lock while waiting for the next AI call
            continue
        origins.add(origin)
        if not items:
            problems.append(f"Could not generate valid items for {clo.code} from its learning outcome - try again")
        for data in items:
            created.append(_new_item(db, user, course, clo, body.purpose, data, origin, call_id))
        db.commit()  # one CLO at a time: short write transactions
    record(db, user, "items.generated", "course", course.id,
           {"purpose": body.purpose, "count": len(created), "origins": sorted(origins), "problems": problems}, course_id=course.id)
    db.commit()
    return {"created": [item_out(db, i) for i in created], "problems": problems, "origins": sorted(origins)}


@router.post("/items", status_code=201)
def create(body: ItemIn, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    if body.purpose not in PURPOSES:
        raise HTTPException(422, f"purpose must be one of {PURPOSES}")
    course = _course(db, user, body.course_id, write=True)
    clo = db.get(CLO, body.clo_id)
    if not clo or clo.course_id != course.id:
        raise HTTPException(404, "CLO not found in this course")
    it = _new_item(db, user, course, clo, body.purpose, body.model_dump(), "manual")
    record(db, user, "items.created", "item", it.id, {"purpose": body.purpose}, course_id=course.id)
    db.commit()
    return item_out(db, it)


@router.get("/items")
def list_items(course_id: int, purpose: str | None = None, status: str | None = None, clo_id: int | None = None,
               db: Session = Depends(get_db), user: User = Depends(require("items:manage", "reports:read"))):
    ensure_course_access(db, user, course_id)
    q = select(Item).where(Item.course_id == course_id).order_by(Item.clo_id, Item.id)
    if purpose:
        q = q.where(Item.purpose == purpose)
    if status:
        q = q.where(Item.status == status)
    if clo_id:
        q = q.where(Item.clo_id == clo_id)
    return [item_out(db, i) for i in db.scalars(q)]


@router.patch("/items/{iid}")
def edit(iid: int, body: ItemPatch, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    """Pending items are edited in place; editing an approved item creates a new pending version."""
    it = db.get(Item, iid)
    if not it:
        raise HTTPException(404, "Item not found")
    course = _course(db, user, it.course_id, write=True)
    merged = {"stem": it.stem, "options": it.options, "answer_index": it.answer, "explanation": it.explanation,
              "hints": it.hints, "difficulty": it.difficulty, "chunk_ids": it.source_chunk_ids}
    merged.update({k: v for k, v in body.model_dump(exclude_unset=True).items()})
    if it.status == "pending":
        v = gen.validate(merged)
        if not v:
            raise HTTPException(422, "Item needs a stem, exactly 4 distinct options and a valid answer index")
        it.stem, it.options, it.answer_index = v["stem"], v["options"], str(v["answer_index"])
        it.explanation, it.hints, it.difficulty = v["explanation"], v["hints"], v["difficulty"]
        record(db, user, "items.edited", "item", it.id, {"fields": list(body.model_dump(exclude_unset=True))}, course_id=course.id)
        db.commit()
        return item_out(db, it)
    if it.status != "approved":
        raise HTTPException(409, f"A {it.status} item cannot be edited")
    new = _new_item(db, user, course, db.get(CLO, it.clo_id), it.purpose, merged, "manual", supersedes=it.id, version=it.version_no + 1)
    record(db, user, "items.new_version", "item", new.id, {"supersedes": it.id}, course_id=course.id)
    db.commit()
    return item_out(db, new)


@router.post("/items/{iid}/review")
def review(iid: int, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    it = db.get(Item, iid)
    if not it:
        raise HTTPException(404, "Item not found")
    _course(db, user, it.course_id, write=True)
    if it.status != "pending":
        raise HTTPException(409, f"Item is already {it.status}")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    if body.decision == "reject" and len(body.note.strip()) < 3:
        raise HTTPException(422, "Give a reason when rejecting")
    it.status = "approved" if body.decision == "approve" else "rejected"
    it.reviewed_by, it.reviewed_at, it.review_note = user.id, utcnow(), body.note
    if it.status == "approved" and it.supersedes_id:
        old = db.get(Item, it.supersedes_id)
        if old and old.status == "approved":
            old.status = "retired"
            old.review_note = f"Replaced by version {it.version_no}"
    ai_gateway.close_review(db, "item", it.id, it.status, user, body.note)
    record(db, user, f"items.{it.status}", "item", it.id, {"note": body.note, "origin": it.origin}, course_id=it.course_id)
    db.commit()
    if automation.enabled() and it.status == "approved" and it.purpose == "diagnostic":
        # Automation: the diagnostic goes live once the teacher has approved enough questions for every CLO.
        automation.publish_if_ready(db, it.course_id, user)
    return item_out(db, it)


@router.delete("/items/{iid}")
def delete_draft(iid: int, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    """Delete a draft (pending) question before the questionnaire is approved. No learner has seen it,
    so it is removed outright; the deletion is kept in the audit trail."""
    it = db.get(Item, iid)
    if not it:
        raise HTTPException(404, "Item not found")
    _course(db, user, it.course_id, write=True)
    if it.status != "pending":
        raise HTTPException(409, "Only draft questions can be deleted; retire an approved question instead")
    clo = db.get(CLO, it.clo_id)
    ai_gateway.close_review(db, "item", it.id, "rejected", user, "Deleted before approval")
    record(db, user, "items.deleted", "item", it.id,
           {"purpose": it.purpose, "clo": clo.code if clo else None, "stem": it.stem[:200], "origin": it.origin}, course_id=it.course_id)
    db.delete(it)
    db.commit()
    return {"deleted": iid}


class ApproveAllIn(BaseModel):
    note: str = ""


@router.post("/items/approve-all/{course_id}")
def approve_questionnaire(course_id: int, body: ApproveAllIn, db: Session = Depends(get_db),
                          user: User = Depends(require("items:manage"))):
    """ONE teacher approval for the whole questionnaire: every remaining draft question of the course, whatever its
    type (diagnostic, practice, reteach), is approved together. The diagnostic then goes live if every CLO has
    enough approved questions."""
    _course(db, user, course_id, write=True)
    drafts = db.scalars(select(Item).where(Item.course_id == course_id, Item.status == "pending").order_by(Item.id)).all()
    if not drafts:
        raise HTTPException(409, "There are no draft questions to approve")
    counts: dict[str, int] = {}
    for it in drafts:
        it.status, it.reviewed_by, it.reviewed_at, it.review_note = "approved", user.id, utcnow(), body.note or "Approved with the questionnaire"
        if it.supersedes_id:
            old = db.get(Item, it.supersedes_id)
            if old and old.status == "approved":
                old.status, old.review_note = "retired", f"Replaced by version {it.version_no}"
        ai_gateway.close_review(db, "item", it.id, "approved", user, "Approved with the questionnaire")
        counts[it.purpose] = counts.get(it.purpose, 0) + 1
    record(db, user, "items.questionnaire_approved", "course", course_id, {"counts": counts, "note": body.note}, course_id=course_id)
    db.commit()
    published = automation.publish_if_ready(db, course_id, user)
    return {"approved": sum(counts.values()), "by_type": counts, "diagnostic_published": published, "coverage": coverage(db, course_id)}


@router.post("/items/{iid}/retire")
def retire(iid: int, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    it = db.get(Item, iid)
    if not it:
        raise HTTPException(404, "Item not found")
    _course(db, user, it.course_id, write=True)
    if it.status != "approved":
        raise HTTPException(409, "Only approved items can be retired")
    it.status, it.review_note = "retired", body.note
    record(db, user, "items.retired", "item", it.id, {"note": body.note}, course_id=it.course_id)
    db.commit()
    return item_out(db, it)


# ---- diagnostic coverage and publication ---------------------------------------------
def get_config(db: Session, course_id: int) -> DiagnosticConfig:
    cfg = db.get(DiagnosticConfig, course_id)
    if not cfg:
        cfg = DiagnosticConfig(course_id=course_id, min_items_per_clo=3, items_per_clo=3, published=False)
        db.add(cfg)
        db.flush()
    return cfg


def coverage(db: Session, course_id: int, purpose: str = "diagnostic") -> dict:
    cfg = get_config(db, course_id)
    rows = []
    for clo in db.scalars(select(CLO).where(CLO.course_id == course_id).order_by(CLO.code)):
        items = db.scalars(select(Item).where(Item.clo_id == clo.id, Item.purpose == purpose)).all()
        approved = [i for i in items if i.status == "approved"]
        rows.append({"clo_id": clo.id, "clo_code": clo.code, "description": clo.description, "approved": len(approved),
                     "pending": sum(1 for i in items if i.status == "pending"),
                     "difficulties": sorted({i.difficulty for i in approved}),
                     "meets_minimum": len(approved) >= cfg.min_items_per_clo})
    return {"course_id": course_id, "purpose": purpose, "min_items_per_clo": cfg.min_items_per_clo,
            "items_per_clo": cfg.items_per_clo, "published": cfg.published, "published_at": cfg.published_at,
            "clos": rows, "complete": bool(rows) and all(r["meets_minimum"] for r in rows),
            "short": [r["clo_code"] for r in rows if not r["meets_minimum"]]}


@router.get("/items/coverage/{course_id}")
def get_coverage(course_id: int, purpose: str = "diagnostic", db: Session = Depends(get_db),
                 user: User = Depends(require("items:manage", "reports:read"))):
    ensure_course_access(db, user, course_id)
    out = coverage(db, course_id, purpose)
    db.commit()
    return out


@router.put("/diagnostic/config/{course_id}")
def set_config(course_id: int, body: ConfigIn, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    _course(db, user, course_id, write=True)
    cfg = get_config(db, course_id)
    if body.items_per_clo > body.min_items_per_clo:
        raise HTTPException(422, "Items asked per CLO cannot exceed the minimum approved per CLO")
    cfg.min_items_per_clo, cfg.items_per_clo = body.min_items_per_clo, body.items_per_clo
    if cfg.published and not coverage(db, course_id)["complete"]:
        cfg.published = False
    record(db, user, "diagnostic.config_changed", "course", course_id, body.model_dump(), course_id=course_id)
    db.commit()
    return coverage(db, course_id)


@router.post("/diagnostic/publish/{course_id}")
def publish(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    _course(db, user, course_id, write=True)
    cov = coverage(db, course_id)
    if not cov["complete"]:
        db.commit()
        raise HTTPException(409, {"message": f"Every CLO needs at least {cov['min_items_per_clo']} approved diagnostic items",
                                  "short": cov["short"]})
    cfg = get_config(db, course_id)
    cfg.published, cfg.published_at, cfg.published_by = True, utcnow(), user.id
    record(db, user, "diagnostic.published", "course", course_id, {"min_items_per_clo": cfg.min_items_per_clo}, course_id=course_id)
    db.commit()
    return coverage(db, course_id)


@router.get("/items/my-courses")
def my_item_courses(db: Session = Depends(get_db), user: User = Depends(require("items:manage"))):
    allowed = accessible_course_ids(db, user)
    q = select(Course).where(Course.status.in_(["approved", "active"])).order_by(Course.code)
    return [{"id": c.id, "code": c.code, "title": c.title, "status": c.status} for c in db.scalars(q) if allowed is None or c.id in allowed]
