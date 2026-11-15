"""NB-2 - Ingest and manage approved teaching sources.

Instructors (for their courses) and admins upload reference material. A
*different* reviewer must approve it before AI generation may use it; new
versions replace old ones on approval, and sources can be retired. Only
approved sources are ever retrieved for grounding.
"""
import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.audit import record
from ..core.crypto import store_encrypted_file
from ..core.rbac import accessible_course_ids, ensure_course_access, require
from ..db import get_db, utcnow
from ..models.outcomes import Course
from ..models.sources import Source, SourceChunk
from ..models.user import User
from ..services import retrieval
from ..services.extraction import MIME, extract_markdown, validate_upload

router = APIRouter(prefix="/api/sources", tags=["sources"])
KINDS = ("textbook", "lecture_notes", "slides", "reference", "other")


class ReviewIn(BaseModel):
    decision: str  # approve | reject
    note: str = ""


class RetireIn(BaseModel):
    reason: str


def out(db: Session, s: Source, chunks: bool = False) -> dict:
    users = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_([x for x in (s.uploaded_by, s.reviewed_by) if x])))}
    n = db.scalar(select(func.count()).select_from(SourceChunk).where(SourceChunk.source_id == s.id))
    c = db.get(Course, s.course_id)
    d = {"id": s.id, "course_id": s.course_id, "course_code": c.code if c else None, "title": s.title, "kind": s.kind,
         "license_note": s.license_note, "filename": s.filename, "version_no": s.version_no, "supersedes_id": s.supersedes_id,
         "status": s.status, "uploaded_by": users.get(s.uploaded_by), "uploaded_by_id": s.uploaded_by,
         "uploaded_at": s.uploaded_at, "reviewed_by": users.get(s.reviewed_by), "reviewed_at": s.reviewed_at,
         "review_note": s.review_note, "chunks": n, "characters": len(s.extracted_markdown)}
    if chunks:
        d["chunk_list"] = [{"id": ch.id, "ordinal": ch.ordinal, "heading": ch.heading, "text": ch.text}
                           for ch in db.scalars(select(SourceChunk).where(SourceChunk.source_id == s.id).order_by(SourceChunk.ordinal))]
        d["extracted_markdown"] = s.extracted_markdown
    return d


@router.post("/upload", status_code=201)
async def upload(
    course_id: int = Form(...),
    title: str = Form(...),
    kind: str = Form("lecture_notes"),
    license_note: str | None = Form(None),
    replaces_id: int | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require("sources:manage")),
):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    ensure_course_access(db, user, course_id, write=True)
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {KINDS}")
    data = await file.read()
    ext = validate_upload(file.filename, data)
    digest = hashlib.sha256(data).hexdigest()
    if db.scalar(select(Source).where(Source.course_id == course_id, Source.sha256 == digest, Source.status.in_(["pending", "approved"]))):
        raise HTTPException(409, "This file is already a source for this course")
    md, _engine = extract_markdown(file.filename, data)
    pieces = retrieval.chunk_markdown(md)
    if not pieces:
        raise HTTPException(422, "No text could be extracted from this file")
    version_no, supersedes = 1, None
    if replaces_id:
        old = db.get(Source, replaces_id)
        if not old or old.course_id != course_id:
            raise HTTPException(422, "The source being replaced must belong to the same course")
        version_no, supersedes = old.version_no + 1, old.id
    s = Source(course_id=course_id, title=title.strip(), kind=kind, license_note=license_note, filename=file.filename,
               mime=MIME[ext], sha256=digest, file_ref=store_encrypted_file(data, file.filename), extracted_markdown=md,
               version_no=version_no, supersedes_id=supersedes, status="pending", uploaded_by=user.id)
    db.add(s)
    db.flush()
    for i, (heading, text) in enumerate(pieces):
        db.add(SourceChunk(source_id=s.id, ordinal=i, heading=heading, text=text))
    record(db, user, "sources.uploaded", "source", s.id, {"title": s.title, "version_no": version_no, "chunks": len(pieces)},
           course_id=course_id)
    db.commit()
    return out(db, s)


@router.get("")
def list_sources(course_id: int | None = None, status: str | None = None, db: Session = Depends(get_db),
                 user: User = Depends(require("sources:manage", "sources:approve", "reports:read"))):
    q = select(Source).order_by(Source.course_id, Source.title, Source.version_no.desc())
    if course_id:
        q = q.where(Source.course_id == course_id)
    if status:
        q = q.where(Source.status == status)
    allowed = accessible_course_ids(db, user)
    return [out(db, s) for s in db.scalars(q) if allowed is None or s.course_id in allowed]


@router.get("/search")
def search(course_id: int, q: str, db: Session = Depends(get_db), user: User = Depends(require("sources:manage", "reports:read"))):
    """Preview what the AI would be grounded on for a question."""
    ensure_course_access(db, user, course_id)
    return [h.__dict__ for h in retrieval.search(db, course_id, q, k=5)]


@router.get("/{sid}")
def get_source(sid: int, db: Session = Depends(get_db), user: User = Depends(require("sources:manage", "sources:approve", "reports:read"))):
    s = db.get(Source, sid)
    if not s:
        raise HTTPException(404, "Source not found")
    ensure_course_access(db, user, s.course_id)
    return out(db, s, chunks=True)


@router.post("/{sid}/review")
def review(sid: int, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(require("sources:approve"))):
    s = db.get(Source, sid)
    if not s:
        raise HTTPException(404, "Source not found")
    ensure_course_access(db, user, s.course_id, write=True)
    if s.status != "pending":
        raise HTTPException(409, f"Source is already {s.status}")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    # separation of duties: someone else approves an upload - unless the uploader is the course's only
    # current instructor (there is nobody else); that approval is marked as self-approved in the audit log
    self_approved = s.uploaded_by == user.id
    if self_approved:
        from .offerings import course_instructors

        others = [i for i in course_instructors(db, s.course_id) if i.id != user.id]
        if others:
            raise HTTPException(409, "Another instructor on this course must approve a source you uploaded (separation of duties)")
    if body.decision == "reject" and len(body.note.strip()) < 5:
        raise HTTPException(422, "Give a reason when rejecting")
    s.status = "approved" if body.decision == "approve" else "rejected"
    s.reviewed_by, s.reviewed_at, s.review_note = user.id, utcnow(), body.note
    if s.status == "approved" and s.supersedes_id:
        old = db.get(Source, s.supersedes_id)
        if old and old.status == "approved":
            old.status = "retired"
            old.review_note = f"Replaced by version {s.version_no}"
    record(db, user, f"sources.{s.status}", "source", s.id, {"note": body.note, "self_approved": self_approved},
           course_id=s.course_id)
    db.commit()
    return out(db, s)


@router.post("/{sid}/retire")
def retire(sid: int, body: RetireIn, db: Session = Depends(get_db), user: User = Depends(require("sources:approve"))):
    s = db.get(Source, sid)
    if not s:
        raise HTTPException(404, "Source not found")
    ensure_course_access(db, user, s.course_id, write=True)
    if s.status != "approved":
        raise HTTPException(409, "Only approved sources can be retired")
    if len(body.reason.strip()) < 5:
        raise HTTPException(422, "A reason is required")
    s.status = "retired"
    s.review_note = body.reason
    record(db, user, "sources.retired", "source", s.id, {"reason": body.reason}, course_id=s.course_id)
    db.commit()
    return out(db, s)
