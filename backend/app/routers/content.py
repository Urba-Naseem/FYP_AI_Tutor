"""US-5.4 - Draft and approve learning content (drafts written from the CLOs/PLOs, optionally enriched with approved sources, faculty approval, version history)."""
import difflib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..core.rbac import ensure_course_access, require
from ..db import get_db, utcnow
from ..models.content import ContentDoc, ContentVersion
from ..models.outcomes import CLO, Course, Unit
from ..models.user import User
from ..services import ai_gateway, automation, course_context, retrieval

router = APIRouter(prefix="/api/content", tags=["content"])

LESSON_SYSTEM = (
    "You are an expert university lecturer writing accessible study material that teaches the given learning "
    "outcomes at their Bloom level, within the scope of the COURSE OUTLINE. If numbered SOURCES are provided, base "
    "the material on them and cite them inline like [S1]. Write in Markdown with headings, short paragraphs, worked examples, "
    "fenced code blocks with a language tag, LaTeX maths in $...$ or $$...$$, and Mermaid diagrams in ```mermaid "
    "blocks where a diagram helps. Do not include quiz answers. Describe every diagram in one sentence of text."
)


class DraftIn(BaseModel):
    course_id: int
    unit_id: int
    kind: str = "lesson"  # lesson | alternate
    clo_id: int | None = None  # required for alternate explanations


class VersionIn(BaseModel):
    body_md: str
    change_summary: str


class ReviewIn(BaseModel):
    decision: str
    note: str = ""


def _approved(db: Session, doc_id: int) -> ContentVersion | None:
    return db.scalar(select(ContentVersion).where(ContentVersion.doc_id == doc_id, ContentVersion.status == "approved")
                     .order_by(ContentVersion.version_no.desc()).limit(1))


def version_out(v: ContentVersion, body=True) -> dict:
    d = {"id": v.id, "doc_id": v.doc_id, "version_no": v.version_no, "status": v.status, "origin": v.origin,
         "change_summary": v.change_summary, "source_chunk_ids": v.source_chunk_ids, "ai_call_id": v.ai_call_id,
         "created_at": v.created_at, "reviewed_at": v.reviewed_at, "review_note": v.review_note}
    if body:
        d["body_md"] = v.body_md
    return d


def doc_out(db: Session, d: ContentDoc, versions=False) -> dict:
    vs = db.scalars(select(ContentVersion).where(ContentVersion.doc_id == d.id).order_by(ContentVersion.version_no.desc())).all()
    appr = next((v for v in vs if v.status == "approved"), None)
    unit = db.get(Unit, d.unit_id)
    clo = db.get(CLO, d.clo_id) if d.clo_id else None
    out = {"id": d.id, "course_id": d.course_id, "unit_id": d.unit_id, "unit_number": unit.number if unit else None,
           "clo_id": d.clo_id, "clo_code": clo.code if clo else None, "kind": d.kind, "title": d.title,
           "approved_version": appr.version_no if appr else None, "latest_version": vs[0].version_no if vs else None,
           "latest_status": vs[0].status if vs else None, "pending": sum(1 for v in vs if v.status == "pending")}
    if versions:
        out["versions"] = [version_out(v) for v in vs]
    return out


def _offline_lesson(unit: Unit, clos: list[CLO], hits) -> str:
    note = ("Offline draft assembled from the approved course sources - review and edit before approving." if hits else
            "Offline outline draft built from the unit's learning outcomes (the AI service was unavailable). "
            "Fill in each section, or draft again with AI later, before approving.")
    parts = [f"# Unit {unit.number}: {unit.title}", "", f"> {note}", "", "## What you will learn", ""]
    parts += [f"- **{c.code}**: {c.description}" for c in clos]
    for i, h in enumerate(hits):
        parts += ["", f"## {h.heading or h.source_title}", "", h.text.strip(), "", f"*Source: [S{i + 1}] {h.source_title}*"]
    if not hits:
        for c in clos:
            parts += ["", f"## {c.code}: {c.description}", "", "*Key ideas, a worked example and a common misconception go here.*"]
    parts += ["", "## Check your understanding", ""]
    parts += [f"- Can you explain, in your own words, how to *{c.description[0].lower() + c.description[1:].rstrip('.')}*?" for c in clos]
    return "\n".join(parts)


def _new_version(db, user, doc: ContentDoc, body: str, origin: str, summary: str, chunk_ids=None, call_id=None) -> ContentVersion:
    last = db.scalar(select(ContentVersion.version_no).where(ContentVersion.doc_id == doc.id).order_by(ContentVersion.version_no.desc()).limit(1)) or 0
    auto = automation.enabled()  # automation: the new version is published at once (review stays optional)
    v = ContentVersion(doc_id=doc.id, version_no=last + 1, body_md=body, status="pending", origin=origin, change_summary=summary,
                       source_chunk_ids=chunk_ids or [], ai_call_id=call_id, created_by=user.id)
    db.add(v)
    db.flush()
    if auto:
        for old in db.scalars(select(ContentVersion).where(ContentVersion.doc_id == doc.id, ContentVersion.status == "approved")):
            old.status = "superseded"
        v.status, v.reviewed_at, v.review_note = "approved", utcnow(), automation.AUTO_NOTE
    ai_gateway.route_review(db, "content", "content_version", v.id, doc.course_id,
                            f"{doc.title} v{v.version_no} ({origin}{', published automatically' if auto else ''})", user, call_id)
    return v


@router.post("/draft", status_code=201)
def draft(body: DraftIn, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    course = db.get(Course, body.course_id)
    unit = db.get(Unit, body.unit_id)
    if not course or not unit or unit.course_id != course.id:
        raise HTTPException(404, "Course or unit not found")
    ensure_course_access(db, user, course.id, write=True)
    if course.status not in ("approved", "active"):
        raise HTTPException(409, "Course outcomes must be approved first")
    if body.kind not in ("lesson", "alternate"):
        raise HTTPException(422, "kind must be lesson or alternate")
    if body.kind == "alternate":
        clo = db.get(CLO, body.clo_id) if body.clo_id else None
        if not clo or clo.course_id != course.id:
            raise HTTPException(422, "An alternate explanation needs a CLO of this course")
        clos = [clo]
        title = f"{clo.code} - another way to understand it"
    else:
        clos = db.scalars(select(CLO).where(CLO.unit_id == unit.id).order_by(CLO.code)).all()
        title = f"Unit {unit.number}: {unit.title}"
    # The lesson is written from the unit's CLOs (and the PLOs they map to); approved teaching sources are an
    # optional extra that is added and cited when it matches.
    query = unit.title + " " + " ".join(c.description for c in clos)
    hits = retrieval.search(db, course.id, query, k=5)
    chunk_ids = [h.chunk_id for h in hits]
    style = ("Write a complete lesson for this unit." if body.kind == "lesson" else
             "Explain this outcome in a DIFFERENT way from a standard lecture: use an everyday analogy, a step-by-step "
             "worked example and a common misconception. Keep it short (about 400 words).")
    sources = f"SOURCES:\n{ai_gateway.format_sources(hits)}\n\n" if hits else ""
    prompt = (f"COURSE OUTLINE:\n{course_context.outline(db, course)}\n\n{sources}UNIT {unit.number}: {unit.title}\n"
              "OUTCOMES TO TEACH:\n" + "\n".join(f"{c.code}: {c.description}" for c in clos) + f"\n\n{style}")
    try:
        md = ai_gateway.call_text(db, user, f"content.draft.{body.kind}", prompt, LESSON_SYSTEM, temperature=0.4,
                                  course_id=course.id, chunk_ids=chunk_ids)
        origin = "ai"
    except ai_gateway.AIUnavailable:
        md = _offline_lesson(unit, clos, hits[:3] if body.kind == "lesson" else hits[1:3] or hits[:1])
        origin = "offline"
    call_id = ai_gateway.last_call_id(db)
    doc = db.scalar(select(ContentDoc).where(ContentDoc.unit_id == unit.id, ContentDoc.kind == body.kind,
                                             ContentDoc.clo_id == (clos[0].id if body.kind == "alternate" else None)))
    if not doc:
        doc = ContentDoc(course_id=course.id, unit_id=unit.id, clo_id=clos[0].id if body.kind == "alternate" else None,
                         kind=body.kind, title=title)
        db.add(doc)
        db.flush()
    v = _new_version(db, user, doc, md, origin, f"{origin} draft", chunk_ids, call_id)
    record(db, user, "content.drafted", "content_version", v.id, {"doc_id": doc.id, "origin": origin, "kind": body.kind}, course_id=course.id)
    db.commit()
    return {**doc_out(db, doc), "version": version_out(v)}


@router.post("/{doc_id}/versions", status_code=201)
def new_version(doc_id: int, body: VersionIn, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    doc = db.get(ContentDoc, doc_id)
    if not doc:
        raise HTTPException(404, "Content not found")
    ensure_course_access(db, user, doc.course_id, write=True)
    if len(body.body_md.strip()) < 20 or len(body.change_summary.strip()) < 3:
        raise HTTPException(422, "Provide the content and a short summary of the change")
    v = _new_version(db, user, doc, body.body_md, "manual", body.change_summary)
    record(db, user, "content.version_created", "content_version", v.id, {"doc_id": doc.id, "summary": body.change_summary}, course_id=doc.course_id)
    db.commit()
    return version_out(v)


@router.post("/versions/{vid}/review")
def review(vid: int, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require("content:manage"))):
    v = db.get(ContentVersion, vid)
    if not v:
        raise HTTPException(404, "Version not found")
    doc = db.get(ContentDoc, v.doc_id)
    ensure_course_access(db, user, doc.course_id, write=True)
    if v.status != "pending":
        raise HTTPException(409, f"Version is already {v.status}")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    if body.decision == "reject" and len(body.note.strip()) < 3:
        raise HTTPException(422, "Give a reason when rejecting")
    if body.decision == "approve":
        for old in db.scalars(select(ContentVersion).where(ContentVersion.doc_id == doc.id, ContentVersion.status == "approved")):
            old.status = "superseded"
    v.status = "approved" if body.decision == "approve" else "rejected"
    v.reviewed_by, v.reviewed_at, v.review_note = user.id, utcnow(), body.note
    ai_gateway.close_review(db, "content_version", v.id, v.status, user, body.note)
    record(db, user, f"content.{v.status}", "content_version", v.id, {"doc_id": doc.id, "version_no": v.version_no, "note": body.note},
           course_id=doc.course_id)
    db.commit()
    return doc_out(db, doc, versions=True)


@router.get("")
def list_content(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("content:manage", "reports:read"))):
    ensure_course_access(db, user, course_id)
    return [doc_out(db, d) for d in db.scalars(select(ContentDoc).where(ContentDoc.course_id == course_id).order_by(ContentDoc.unit_id, ContentDoc.kind))]


@router.get("/{doc_id}")
def get_doc(doc_id: int, db: Session = Depends(get_db), user: User = Depends(require("content:manage", "reports:read"))):
    doc = db.get(ContentDoc, doc_id)
    if not doc:
        raise HTTPException(404, "Content not found")
    ensure_course_access(db, user, doc.course_id)
    return doc_out(db, doc, versions=True)


@router.get("/versions/{a}/diff/{b}")
def diff(a: int, b: int, db: Session = Depends(get_db), user: User = Depends(require("content:manage", "reports:read"))):
    va, vb = db.get(ContentVersion, a), db.get(ContentVersion, b)
    if not va or not vb or va.doc_id != vb.doc_id:
        raise HTTPException(404, "Versions not found in the same document")
    ensure_course_access(db, user, db.get(ContentDoc, va.doc_id).course_id)
    lines = difflib.unified_diff(va.body_md.splitlines(), vb.body_md.splitlines(), f"v{va.version_no}", f"v{vb.version_no}", lineterm="")
    return {"diff": "\n".join(lines)}
