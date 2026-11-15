"""US-4.2 - Item generation (Gemini) from the course outline (CLOs/PLOs), optionally enriched with approved
teaching sources, plus a deterministic offline generator that works from those sources when they exist."""
from __future__ import annotations

import hashlib
import random
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.items import Item
from ..models.outcomes import CLO, Course
from . import ai_gateway, retrieval

ITEM_SYSTEM = (
    "You write fair multiple-choice assessment items for university courses. Every item must test the given "
    "learning outcome at its Bloom level, within the scope of the COURSE OUTLINE. If numbered SOURCES are provided, "
    "base the items on them and cite them in explanations like [S1]. Each item has exactly 4 distinct options and one correct answer. "
    "Hints must guide thinking (Socratic) and must NEVER state or point directly to the correct option."
)


def protected_answers(db: Session, course_id: int) -> list[str]:
    """Correct option texts of live items - the tutor must never reveal them."""
    out = []
    for it in db.scalars(select(Item).where(Item.course_id == course_id, Item.status.in_(["approved", "pending"]))):
        try:
            out.append(it.options[it.answer])
        except (ValueError, IndexError):
            pass
    return out


def validate(raw: dict) -> dict | None:
    """Return a clean item dict, or None if unusable."""
    try:
        stem = str(raw["stem"]).strip()
        options = [str(o).strip() for o in raw["options"]]
        ans = int(raw["answer_index"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(stem) < 10 or len(options) != 4 or len({o.lower() for o in options}) != 4 or not all(options):
        return None
    if not 0 <= ans < 4:
        return None
    correct = options[ans].lower()
    hints = [str(h).strip() for h in raw.get("hints") or [] if str(h).strip()]
    hints = [h for h in hints if correct not in h.lower()][:3]  # answer protection on hints
    diff = raw.get("difficulty") or 2
    return {"stem": stem, "options": options, "answer_index": ans, "explanation": str(raw.get("explanation") or "").strip(),
            "hints": hints, "difficulty": min(3, max(1, int(diff)))}


PURPOSE_NOTE = {
    "diagnostic": "diagnostic (checks what the student already knows before teaching)",
    "formative": "formative practice (used while learning)",
    "reteach": "reteach (simpler, re-checks the outcome after a re-explanation)",
}


def llm_prompt(course: Course, clo: CLO, hits, n: int, purpose: str, outline: str = "") -> str:
    """Prompt built from the course outline (CLOs/PLOs); optional teaching sources are added when they match.
    Static parts come first (outline, sources), the per-request part last, so repeated calls share a prefix."""
    sources = f"SOURCES:\n{ai_gateway.format_sources(hits)}\n\n" if hits else ""
    cite = " Cite sources in explanations like [S1]." if hits else ""
    return (
        f"COURSE OUTLINE:\n{outline or f'COURSE: {course.code} {course.title}'}\n\n{sources}"
        f"LEARNING OUTCOME {clo.code}: {clo.description}\n"
        f"Write {n} {purpose} multiple-choice items that test this outcome, with a spread of difficulty 1 (easy) to 3 (hard). "
        f"Use Markdown in stems where helpful (code in backticks, maths in $...$).{cite} Reply as JSON "
        '{"items":[{stem, options (4), answer_index, explanation, hints, difficulty}]}.'
    )


def bundle_prompt(outline: str, clo: CLO, counts: dict[str, int], hits) -> str:
    """One request for every question type a CLO still needs (optimisation 1: fewer, batched calls).
    `outline` is the whole-course outline, identical for every CLO of the course, so it is sent first (shared prefix)."""
    sources = f"SOURCES:\n{ai_gateway.format_sources(hits)}\n\n" if hits else ""
    cite = " Cite sources in explanations like [S1]." if hits else ""
    wanted = "; ".join(f"{n} {PURPOSE_NOTE.get(p, p)} items in '{p}'" for p, n in counts.items() if n > 0)
    return (
        f"COURSE OUTLINE:\n{outline}\n\n{sources}"
        f"QUESTION SET FOR {clo.code}: {clo.description}\n"
        f"Write {wanted}. Every item tests {clo.code} at its Bloom level. In each list use about equal numbers of "
        "difficulty 1 (easy), 2 (medium) and 3 (hard) items, so every student can get questions at their own level; "
        "no item repeats another, across all lists. Use Markdown in stems where helpful (code in backticks, maths in $...$)."
        f"{cite} Reply as JSON with the lists diagnostic, formative and reteach (leave a list empty if none were asked for)."
    )


def bundle_job(db: Session, course: Course, clo: CLO, counts: dict[str, int], outline: str) -> dict:
    """A call_json_many job for one CLO's missing questions (see bundle_prompt)."""
    from ..config import get_settings
    from .schemas import ItemBundleOut

    hits = retrieval.search(db, course.id, f"{clo.description} {clo.code}", k=4)
    return {"purpose": "items.generate.bundle", "prompt": bundle_prompt(outline, clo, counts, hits), "system": ITEM_SYSTEM,
            "schema": ItemBundleOut, "temperature": 0.0, "course_id": course.id, "chunk_ids": [h.chunk_id for h in hits],
            "budget": get_settings().gemini_background_timeout_seconds}


def bundle_items(data: dict, counts: dict[str, int], chunk_ids) -> dict[str, list[dict]]:
    """Validated, de-duplicated items per purpose from a bundle reply (at most the number asked for)."""
    out, seen = {}, set()
    for purpose, n in counts.items():
        kept = []
        for raw in (data or {}).get(purpose) or []:
            v = validate(raw) if isinstance(raw, dict) else None
            if not v or v["stem"].lower() in seen:
                continue
            seen.add(v["stem"].lower())
            v["chunk_ids"] = list(chunk_ids or [])
            kept.append(v)
        out[purpose] = kept[:max(0, n)]
    return out


# ---- offline generator: cloze items from approved source sentences -------------
GENERIC = set("""because gives give refers true false further every after before first second then them they
about above below other same often must should would could being been were make makes made like just still
always never number numbers example examples following follows usual means mean called call calls""".split())


def _key_terms(clo: CLO, hits) -> list[str]:
    from collections import Counter

    text = " ".join(h.text for h in hits)
    counts = Counter(t for t in retrieval.tokens(text) if len(t) >= 4 and not t.isdigit() and t not in GENERIC)
    clo_terms = [t for t in retrieval.tokens(clo.description) if len(t) >= 4 and t not in GENERIC]
    terms = [t for t in clo_terms if counts[t]] + [t for t, _ in counts.most_common(40)]
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def offline_items(clo: CLO, hits, n: int) -> list[dict]:
    terms = _key_terms(clo, hits)
    if len(terms) < 4:
        return []
    rng = random.Random(int(hashlib.sha1(f"{clo.id}".encode()).hexdigest(), 16))
    sentences = []
    for h in hits:
        for s in re.split(r"(?<=[.!?])\s+", re.sub(r"```.*?```", " ", h.text, flags=re.S).replace("\n", " ")):
            s = s.strip()
            if 40 <= len(s) <= 260 and not s.startswith(("|", "#")):
                sentences.append((h, s))
    out, used = [], set()
    for h, s in sentences:
        for t in terms:
            m = re.search(rf"\b{re.escape(t)}\b", s, re.I)
            if not m or (s, t) in used or t in {u[1] for u in used}:
                continue
            distract = [x for x in terms if x != t and not re.search(rf"\b{re.escape(x)}\b", s, re.I)]
            if len(distract) < 3:
                continue
            opts = [t] + rng.sample(distract[:12], 3)
            rng.shuffle(opts)
            stem = "Complete the statement from the course notes:\n\n> " + s[:m.start()] + "\\_\\_\\_\\_\\_" + s[m.end():]
            out.append({"stem": stem, "options": opts, "answer_index": opts.index(t),
                        "explanation": f"The approved notes ({h.source_title}, {h.heading}) state: \"{s}\"",
                        "hints": [f"Re-read the section \"{h.heading}\" in {h.source_title}.",
                                  "Which option makes the statement technically accurate, not just grammatical?"],
                        "difficulty": 1 if t in clo.description.lower() else 2, "chunk_ids": [h.chunk_id]})
            used.add((s, t))
            break
        if len(out) >= n:
            break
    return out


def generate_for_clo(db: Session, user, course: Course, clo: CLO, n: int, purpose: str) -> tuple[list[dict], str, int | None]:
    """Returns (items, origin, ai_call_id). Works from the CLO and the course outline; approved teaching sources
    are added when they match. Raises ValueError only when the AI is unavailable AND no source exists to fall back on."""
    from . import course_context

    hits = retrieval.search(db, course.id, f"{clo.description} {clo.code}", k=4)
    chunk_ids = [h.chunk_id for h in hits]
    outline = course_context.outline(db, course, [clo])
    try:
        from .schemas import ItemsOut

        data = ai_gateway.call_json(db, user, f"items.generate.{purpose}", llm_prompt(course, clo, hits, n, purpose, outline),
                                    ITEM_SYSTEM, temperature=0.0, course_id=course.id, chunk_ids=chunk_ids, schema=ItemsOut)
        items = [v for v in (validate(x) for x in ai_gateway.as_list(data, "items") if isinstance(x, dict)) if v]
        for it in items:
            it["chunk_ids"] = chunk_ids
        if not items:
            raise ai_gateway.AIUnavailable("model returned no valid items")
        return items[:n], "ai", ai_gateway.last_call_id(db)
    except ai_gateway.AIUnavailable:
        if not hits:
            raise ValueError(f"The AI service is unavailable, so items for {clo.code} could not be written from the course "
                             "outcomes. Try again later (optional teaching sources also allow offline generation).")
        return offline_items(clo, hits, n), "offline", ai_gateway.last_call_id(db)
