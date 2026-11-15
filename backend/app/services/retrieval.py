"""NB-2 / US-13.2 - Chunking and keyword (BM25) retrieval over APPROVED sources only."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.sources import Source, SourceChunk

STOP = set("""a an and are as at be by can for from has have how i in into is it its of on or that the their them then there
these this to was we what when where which while who why will with you your does do not use using used each than so if
such may also any all more most other some only very""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]


def chunk_markdown(md: str, size: int = 900, overlap: int = 120) -> list[tuple[str | None, str]]:
    """Split by headings, then into ~size-character windows."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in md.splitlines():
        if re.match(r"^#{1,4}\s", line):
            sections.append((line.lstrip("#").strip(), []))
        else:
            sections[-1][1].append(line)
    out = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        start = 0
        while start < len(body):
            piece = body[start:start + size]
            if start + size < len(body):
                cut = piece.rfind("\n\n")
                if cut > size // 2:
                    piece = piece[:cut]
            out.append((heading, piece.strip()))
            if start + len(piece) >= len(body):
                break
            start += max(len(piece) - overlap, 1)
    return out


@dataclass
class Hit:
    chunk_id: int
    source_id: int
    source_title: str
    heading: str | None
    text: str
    score: float


def search(db: Session, course_id: int, query: str, k: int = 4) -> list[Hit]:
    rows = db.execute(
        select(SourceChunk, Source).join(Source, Source.id == SourceChunk.source_id)
        .where(Source.course_id == course_id, Source.status == "approved")
    ).all()
    if not rows:
        return []
    q = tokens(query)
    if not q:
        return []
    docs = [(c, s, tokens((c.heading or "") + " " + c.text)) for c, s in rows]
    n = len(docs)
    avg = sum(len(d) for *_, d in docs) / n or 1
    df = Counter(t for *_, d in docs for t in set(d))
    k1, b = 1.5, 0.75
    hits = []
    for c, s, d in docs:
        tf = Counter(d)
        score = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        if score > 0:
            hits.append(Hit(c.id, s.id, s.title, c.heading, c.text, round(score, 3)))
    hits.sort(key=lambda h: -h.score)
    return hits[:k]
