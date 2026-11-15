"""Course outline context for AI generation.

The AI runs on the curriculum extracted from the uploaded outline: the course, its units, its CLOs
(with Bloom level) and the PLOs each CLO maps to. Teaching sources are an OPTIONAL extra that an
instructor may add as suggested reading; when approved sources match, callers add them on top of
this outline (and cite them), but nothing requires them.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.mappings import CloPloMapping
from ..models.outcomes import CLO, PLO, Course, Unit
from . import retrieval


def plos_for(db: Session, course: Course, clos: list[CLO]) -> dict[int, list[PLO]]:
    """PLOs mapped to each CLO (approved or suggested mappings; rejected ones are ignored)."""
    ids = [c.id for c in clos]
    if not ids:
        return {}
    rows = db.execute(
        select(CloPloMapping.clo_id, PLO).join(PLO, PLO.id == CloPloMapping.plo_id)
        .where(CloPloMapping.clo_id.in_(ids), CloPloMapping.status != "rejected")
    ).all()
    out: dict[int, list[PLO]] = {}
    for clo_id, plo in rows:
        out.setdefault(clo_id, []).append(plo)
    return out


def outline(db: Session, course: Course, focus: list[CLO] | None = None) -> str:
    """Plain-text course outline for prompts. `focus` lists the CLOs the request is about (all CLOs if omitted)."""
    units = {u.id: u for u in db.scalars(select(Unit).where(Unit.course_id == course.id).order_by(Unit.number))}
    clos = focus if focus is not None else db.scalars(select(CLO).where(CLO.course_id == course.id).order_by(CLO.code)).all()
    mapped = plos_for(db, course, list(clos))
    lines = [f"COURSE: {course.code} {course.title}"]
    if course.description:
        lines.append(f"DESCRIPTION: {course.description.strip()}")
    if units:
        lines.append("UNITS: " + "; ".join(f"Unit {u.number}: {u.title}" for u in units.values()))
    lines.append("COURSE LEARNING OUTCOMES (CLOs):")
    for c in clos:
        unit = units.get(c.unit_id)
        extra = [f"Bloom level: {c.bloom}"] if c.bloom else []
        if unit:
            extra.append(f"Unit {unit.number}")
        lines.append(f"- {c.code}: {c.description}" + (f" ({', '.join(extra)})" if extra else ""))
        for p in mapped.get(c.id, []):
            lines.append(f"    supports {p.code}: {p.description}")
    return "\n".join(lines)


def relevant_clos(db: Session, course_id: int, text: str, k: int = 2) -> list[CLO]:
    """CLOs whose wording overlaps the text most (used for offline answers when no source matches)."""
    q = set(retrieval.tokens(text))
    if not q:
        return []
    scored = []
    for c in db.scalars(select(CLO).where(CLO.course_id == course_id)):
        overlap = len(q & set(retrieval.tokens(c.description)))
        if overlap:
            scored.append((overlap, c.code, c))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for *_, c in scored[:k]]
