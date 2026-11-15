"""Semester roadmap files (study plans): read them and match their courses to the curriculum.

A roadmap is usually a table with one column per semester ("Semester 1" ... "Semester 8"), each cell a course name
with its credit split, e.g. "Programming Fundamentals (3+1)". Course codes are often missing, so courses are
matched to the curriculum's courses by name.

Reading:
  PDF   text positions (pypdfium2): every line is placed in the semester column whose header is nearest; lines of a
        wrapped name are joined; a cell ends at its credit marker "(3+1)" or at a larger vertical gap. The title,
        the "NN Credit Hrs." rows and the colour legend are ignored.
  XLSX  the row with "Semester N" headers gives the columns; every non-empty cell below is a course.
  MD / TXT  "Semester N: A, B" lists, or "Semester N" headings followed by list items.
  other / nothing found: the extracted text is given to the AI (if available) to rebuild the table.

Matching: course code if present, otherwise name similarity (case, "&"/"and", roman numerals, word order and small
words ignored); each curriculum course is matched at most once; the AI can pair what is left. Courses in the roadmap
that are not in the curriculum (no CLOs: general education, electives...) are kept for display only.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED = {".pdf": b"%PDF", ".xlsx": b"PK\x03\x04", ".docx": b"PK\x03\x04", ".md": None, ".txt": None, ".html": None}
RX_SEM = re.compile(r"^\s*(?:semester|sem\.?|term)\s*[-:#]?\s*(\d{1,2})\b", re.I)
RX_YEAR = re.compile(r"^\s*year\s*(\d)\s*[,:–—-]?\s*(fall|autumn|spring|first|second|1st|2nd)\b", re.I)
RX_CREDITS = re.compile(r"\((\d{1,2})(?:\s*\+\s*(\d{1,2}))?\)\s*$")
RX_CREDIT_ROW = re.compile(r"^\s*(?:\d{1,3}\s*)?credit\s*h(?:ou)?rs?\.?\s*$", re.I)
RX_TRAILING_CREDIT_HRS = re.compile(r"\s*\d{1,3}\s*credit\s*h(?:ou)?rs?\.?\s*$", re.I)
RX_CONTROL = re.compile(r"[\x00-\x1f\x7f­]")  # PDF hyphenation / control characters
RX_CODE = re.compile(r"\b([A-Z]{2,5})[-\s]?(\d{3,4}[A-Z]?)\b")
LEGEND = {"computing core", "mathematics supporting", "general education requirement", "elective supporting courses",
          "domain core", "domain elective", "university elective", "general education", "core", "elective"}
SMALL = {"and", "of", "the", "to", "in", "for", "a", "an", "with", "on"}
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


@dataclass
class Entry:
    semester: int
    name: str
    credits: int | None = None
    credits_text: str | None = None
    position: int = 0
    code: str | None = None  # a course code written in the roadmap itself, if any


@dataclass
class Match:
    entry: Entry
    course_code: str | None = None
    score: float = 0.0
    by: str = "none"  # code | exact | similar | ai | manual | none


@dataclass
class Result:
    entries: list[Entry] = field(default_factory=list)
    engine: str = ""


class RoadmapError(ValueError):
    pass


def validate(filename: str, data: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise RoadmapError(f"Unsupported roadmap file '{ext or 'none'}'. Allowed: {', '.join(sorted(ALLOWED))}")
    if not data:
        raise RoadmapError("The roadmap file is empty")
    magic = ALLOWED[ext]
    if magic and not data.startswith(magic):
        raise RoadmapError(f"The roadmap file content does not match its '{ext}' extension")
    return ext


# ---- entry building -----------------------------------------------------------------------------
def _entry(semester: int, text: str, position: int) -> Entry | None:
    text = re.sub(r"\s+", " ", RX_CONTROL.sub("", text)).strip(" -–—•*|\t")
    text = RX_TRAILING_CREDIT_HRS.sub("", text).strip()
    if not text or RX_CREDIT_ROW.match(text) or re.fullmatch(r"\d{1,3}", text):
        return None
    credits = credits_text = None
    if m := RX_CREDITS.search(text):
        th, lab = int(m.group(1)), int(m.group(2) or 0)
        credits, credits_text = th + lab, f"{th}+{lab}" if m.group(2) else str(th)
        text = text[:m.start()].strip(" -–—")
    if not text or (credits is None and _norm(text) in LEGEND):  # legend labels never carry credits
        return None
    code = None
    if m := RX_CODE.search(text):
        code = (m.group(1) + m.group(2)).upper()
    return Entry(semester=semester, name=text, credits=credits, credits_text=credits_text, position=position, code=code)


def _semester_of(text: str) -> int | None:
    if m := RX_SEM.match(text):
        return int(m.group(1))
    if m := RX_YEAR.match(text):
        return (int(m.group(1)) - 1) * 2 + (1 if m.group(2).lower() in ("fall", "autumn", "first", "1st") else 2)
    return None


# ---- PDF (text positions) -----------------------------------------------------------------------
def lines_to_entries(lines: list[tuple[float, float, float, str]]) -> list[Entry]:
    """Pure layout step (tested directly): `lines` = (left, top, right, text) in PDF points, y grows upwards."""
    headers = [(l, t, r, s) for l, t, r, txt in lines if (s := _semester_of(txt)) is not None and len(txt) <= 30]
    if len(headers) < 2:
        return []
    header_top = min(t for _, t, _, _ in headers)
    centers = sorted(((l + r) / 2, s) for l, _, r, s in headers)
    # The legend sits below the table, above a second row of "NN Credit Hrs." totals: cut it off.
    credit_rows = sorted({round(t) for l, t, r, txt in lines if RX_CREDIT_ROW.match(txt) and t < header_top - 1}, reverse=True)
    cutoff = None
    if len(credit_rows) >= 2:
        cutoff = credit_rows[-1] + 30  # everything at or below the legend's labels
    columns: dict[int, list[tuple[float, str]]] = {}
    for l, t, r, txt in lines:
        if t >= header_top - 1 or not txt.strip() or RX_CREDIT_ROW.match(txt) or re.fullmatch(r"\s*\d{1,3}\s*", txt):
            continue
        if cutoff is not None and t <= cutoff:
            continue
        cx = (l + r) / 2
        sem = min(centers, key=lambda c: abs(c[0] - cx))[1]
        columns.setdefault(sem, []).append((t, txt.strip()))
    out: list[Entry] = []
    for sem in sorted(columns):
        rows = sorted(columns[sem], key=lambda x: -x[0])
        gaps = [rows[i][0] - rows[i + 1][0] for i in range(len(rows) - 1)]
        line_gap = min([g for g in gaps if g > 2] or [10.0])
        cell: list[str] = []
        prev_top = None
        pos = 0

        def flush():
            nonlocal cell, pos
            if cell:
                e = _entry(sem, " ".join(cell), pos)
                if e:
                    out.append(e)
                    pos += 1
            cell = []

        for top, txt in rows:
            if prev_top is not None and (prev_top - top > line_gap * 1.5 or (cell and RX_CREDITS.search(cell[-1]))):
                flush()
            cell.append(txt)
            prev_top = top
        flush()
    return out


def _pdf_lines(data: bytes) -> list[list[tuple[float, float, float, str]]]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:  # installed with Docling
        raise RoadmapError("PDF roadmaps need pypdfium2 (pip install -r requirements-docling.txt)") from e
    pages = []
    doc = pdfium.PdfDocument(data)
    try:
        for page in doc:
            pages.append(_page_lines(page.get_textpage()))
    finally:
        doc.close()
    return pages


def _page_lines(tp) -> list[tuple[float, float, float, str]]:
    """One entry per text run (pdfium's rectangles). The text is read through the vertical MIDDLE of each run's box
    only, so letters of the lines just above/below cannot leak in when lines sit closer than the font height."""
    lines: list[tuple[float, float, float, str]] = []
    for i in range(tp.count_rects()):
        left, bottom, right, top = tp.get_rect(i)
        mid, half = (top + bottom) / 2, max((top - bottom) * 0.15, 0.5)
        txt = tp.get_text_bounded(left, mid - half, right, mid + half).strip()
        if txt:
            lines.append((left, top, right, txt))
    return lines


def _pdf_text(data: bytes) -> str:
    return "\n".join(t for page in _pdf_lines(data) for *_, t in page)


# ---- XLSX ----------------------------------------------------------------------------------------
def _xlsx_entries(data: bytes) -> list[Entry]:
    import openpyxl  # installed with Docling

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out: list[Entry] = []
    for ws in wb.worksheets:
        rows = [[("" if v is None else str(v)).strip() for v in r] for r in ws.iter_rows(values_only=True)]
        for i, row in enumerate(rows):
            cols = {j: s for j, v in enumerate(row) if (s := _semester_of(v)) is not None}
            if len(cols) < 2:
                continue
            pos = {s: 0 for s in cols.values()}
            for below in rows[i + 1:]:
                for j, s in cols.items():
                    if j < len(below) and below[j]:
                        if (e := _entry(s, below[j].replace("\n", " "), pos[s])):
                            out.append(e)
                            pos[s] += 1
            break
    return out


# ---- text -----------------------------------------------------------------------------------------
def text_entries(text: str) -> list[Entry]:
    out: list[Entry] = []
    current, pos = None, {}
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:[#>*|-]+|\d+[.)])\s*", "", raw).replace("**", "").strip()
        if not line:
            continue
        sem = _semester_of(line)
        if sem is not None:
            rest = re.split(r"[:–—]", line, maxsplit=1)
            current = sem
            if len(rest) == 2 and rest[1].strip():
                for part in re.split(r"[;,]", rest[1]):
                    if (e := _entry(sem, part, pos.get(sem, 0))):
                        out.append(e)
                        pos[sem] = pos.get(sem, 0) + 1
            continue
        if current is not None and raw.lstrip().startswith(("-", "*", "•")) or (current is not None and RX_CREDITS.search(line)):
            if (e := _entry(current, line, pos.get(current, 0))):
                out.append(e)
                pos[current] = pos.get(current, 0) + 1
    return out


RX_LIST_SEP = re.compile(r"\s*[•·▪�;]\s*")
RX_SECTION_END = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]\s+[A-Z]|#{1,6}\s|[A-Z]{2,6}[-\s]?\d{2,4}[A-Z]?\s*[:\-–—]\s*[A-Za-z])")


def embedded_entries(text: str) -> list[Entry]:
    """A semester roadmap written INSIDE the curriculum document, as rows like
    'Semester 1  Programming Fundamentals • Discrete Structures • ...' (a row may wrap onto following lines).
    Needs at least two semester rows that list courses; otherwise [] (the document has no roadmap)."""
    rows: dict[int, list[str]] = {}
    order: list[int] = []
    current = None
    for raw in text.splitlines():
        line = re.sub(r"^\s*[|>*-]+\s*", "", raw).replace("**", "").strip().strip("|").strip()
        m = RX_SEM.match(line)
        rest = line[m.end():].strip(" :–—-|") if m else ""
        if m and rest and not re.match(r"(?i)courses?\b", rest):
            current = int(m.group(1))
            if current not in rows:
                order.append(current)
            rows.setdefault(current, []).append(rest)
            continue
        if current is None:
            continue
        if not line or RX_SECTION_END.match(line):  # the table ended
            if len(rows) >= 2:
                break
            rows, order, current = {}, [], None
            continue
        rows[current].append(line)  # a wrapped row continues
    if len(rows) < 2:
        return []
    joined = {s: re.sub(r"\s+", " ", " ".join(parts)).strip() for s, parts in rows.items()}
    bullets = any(RX_LIST_SEP.search(t) for t in joined.values())
    out: list[Entry] = []
    for s in order:
        parts = RX_LIST_SEP.split(joined[s]) if bullets else re.split(r"\s*,\s*", joined[s])
        for i, part in enumerate(p for p in parts if p.strip()):
            if (e := _entry(s, part, i)):
                out.append(e)
    return out if len({e.semester for e in out}) >= 2 else []


def rows_to_entries(rows) -> list[Entry]:
    """Entries from AI rows [{semester, name, credits}]."""
    return [e for i, r in enumerate(rows or []) if isinstance(r, dict)
            and (e := _entry(int(r.get("semester") or 0),
                             f"{r.get('name', '')} ({r['credits']})" if r.get("credits") else str(r.get("name", "")), i))
            and e.semester > 0]


# words that place a course in time, in any study-plan layout ("Semester 3", "Term II", "Year 2 - Fall", "Sem. 5")
RX_PERIOD = re.compile(r"\b(?:semester|term|trimester|quarter|year|sem\.?)\s*[-#:]?\s*(?:\d{1,2}|[ivx]{1,4}\b|first|"
                       r"second|third|fourth|fifth|sixth|seventh|eighth)", re.I)


def roadmap_passages(text: str, max_chars: int = 15_000) -> str:
    """The parts of a document that look like a study plan (lines naming semesters / terms / years, with a few lines
    around each), for the AI to read when no roadmap layout was recognised. '' if the document hardly mentions any."""
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if RX_PERIOD.search(ln)]
    if len({re.sub(r"\s+", " ", lines[i].strip().lower()) for i in hits}) < 2:  # at least two different period lines
        return ""
    keep = sorted({k for i in hits for k in range(max(0, i - 2), min(len(lines), i + 8))})
    return "\n".join(lines[k] for k in keep)[:max_chars]


def read(filename: str, data: bytes, ai=None) -> Result:
    """Read a roadmap file. `ai(text) -> list[dict]` is an optional fallback that rebuilds the table from text."""
    ext = validate(filename, data)
    entries, engine, text = [], ext.lstrip("."), ""
    if ext == ".pdf":
        for page in _pdf_lines(data):
            entries += lines_to_entries(page)
        engine = "pdf-layout"
        if not entries:
            text = _pdf_text(data)
    elif ext == ".xlsx":
        entries, engine = _xlsx_entries(data), "xlsx"
    elif ext in (".md", ".txt"):
        text = data.decode("utf-8", errors="replace")
        entries, engine = text_entries(text), "text"
    else:  # docx / html: extract text first
        from .extraction import extract_markdown

        text, _ = extract_markdown(filename, data)
        entries, engine = text_entries(text), "text"
    if not entries and text.strip() and ai is not None:
        try:
            entries, engine = rows_to_entries(ai(text)), "ai"
        except Exception:  # noqa: BLE001 - the AI is a best-effort fallback
            entries = []
    if not entries:
        raise RoadmapError("No semester roadmap could be read from this file. It needs 'Semester 1', 'Semester 2'... "
                           "headers (one column or section per semester).")
    return Result(entries=entries, engine=engine)


# ---- matching --------------------------------------------------------------------------------------
def _norm(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)  # credits / notes in brackets
    s = re.sub(r"[-_/.,:]", " ", s)
    words = [ROMAN.get(w, w) for w in re.findall(r"[a-z0-9]+", s)]
    words = [w for w in words if w not in SMALL]
    return " ".join(words)


def similarity(a: str, b: str) -> float:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    return len(ta & tb) / max(len(ta), len(tb))


SMALL_WORDS = {"of", "and", "the", "in", "to", "for", "&"}


def _expand_acronyms(name: str, title: str) -> str:
    """'Application of ICT' -> 'Application of Information and Communication Technologies' when the course title's
    initials spell the acronym."""
    words = re.findall(r"[A-Za-z]+", title)
    for acro in re.findall(r"\b[A-Z]{2,6}\b", name):
        for i in range(len(words)):
            picked, j = [], i
            while j < len(words) and len(picked) < len(acro):
                if words[j].lower() not in SMALL_WORDS:
                    picked.append(words[j])
                j += 1
            if len(picked) == len(acro) and "".join(w[0] for w in picked).upper() == acro:
                name = name.replace(acro, " ".join(words[i:j]))
                break
    return name


def name_score(name: str, title: str) -> float:
    """Best similarity of a roadmap name to a course title: as written, the part in brackets
    ('Domain Elective 1 (Web Application Development)'), the part before them, and with acronyms spelled out."""
    inner = re.findall(r"\(([^)]{4,})\)", name)
    outer = re.sub(r"\([^)]*\)", " ", name).strip()
    forms = [name, outer, *inner, _expand_acronyms(name, title)]
    return max(similarity(f, title) for f in forms if f)


def match(entries: list[Entry], courses: list[tuple[str, str]], threshold: float = 0.6) -> list[Match]:
    """courses = [(code, title)]. Greedy best-first, each course used once."""
    results = [Match(entry=e) for e in entries]
    used: set[str] = set()
    codes = {c for c, _ in courses}
    for m in results:  # 1. codes written in the roadmap
        if m.entry.code and m.entry.code in codes and m.entry.code not in used:
            m.course_code, m.score, m.by = m.entry.code, 1.0, "code"
            used.add(m.entry.code)
    pairs = sorted(((name_score(m.entry.name, title), i, code) for i, m in enumerate(results) if not m.course_code
                    for code, title in courses), key=lambda x: -x[0])
    for score, i, code in pairs:  # 2. names
        if score < threshold:
            break
        m = results[i]
        if m.course_code or code in used:
            continue
        m.course_code, m.score, m.by = code, round(score, 2), "exact" if score == 1.0 else "similar"
        used.add(code)
    return results


# ---- storage: roadmap rows per curriculum version, applied to the courses' semesters ----------------
AI_READ_SYSTEM = ("You read university study plans. Copy course names exactly. Never invent courses. "
                  'Return JSON: {"courses":[{"semester":1,"name":"Programming Fundamentals","credits":"3+1"}]}')
AI_PAIR_SYSTEM = ("You match courses in a study plan to courses of a curriculum by meaning of the title. Only pair "
                  "courses that are clearly the same course; leave the rest unpaired. "
                  'Return JSON: {"pairs":[{"roadmap":"exact roadmap name","code":"CS1234"}]}')


def version_courses(db, version) -> list[tuple[str, str]]:
    """(code, title) of a version's courses: the reviewed courses if they exist, else the parsed outline."""
    from sqlalchemy import select

    from ..models.outcomes import Course
    from .curriculum_parser import normalise

    rows = db.scalars(select(Course).where(Course.curriculum_version_id == version.id)).all()
    if rows:
        return [(c.code, c.title) for c in rows]
    return [(c["code"], c["title"]) for c in normalise(version.parsed or {}).get("courses", []) if c.get("code")]


def ai_reader(db, user):
    from . import ai_gateway

    def read_text(text: str) -> list[dict]:
        from .schemas import RoadmapRowsOut

        data = ai_gateway.call_json(db, user, "roadmap.read", "Extract every course and its semester from this study "
                                    "plan:\n\n" + text[:60_000], AI_READ_SYSTEM, schema=RoadmapRowsOut)
        return [x for x in ai_gateway.as_list(data, "courses") if isinstance(x, dict)]
    return read_text


def ai_pair(db, user, matches: list[Match], courses: list[tuple[str, str]]) -> None:
    """Let the AI pair what name similarity left open (best effort; skipped when the AI is unavailable)."""
    from . import ai_gateway

    used = {m.course_code for m in matches if m.course_code}
    open_courses = [(c, t) for c, t in courses if c not in used]
    open_entries = [m for m in matches if not m.course_code]
    if not open_courses or not open_entries:
        return
    prompt = ("STUDY PLAN COURSES:\n" + "\n".join(f"- {m.entry.name}" for m in open_entries) +
              "\n\nCURRICULUM COURSES:\n" + "\n".join(f"- {c}: {t}" for c, t in open_courses))
    try:
        from .schemas import PairsOut

        data = ai_gateway.call_json(db, user, "roadmap.match", prompt, AI_PAIR_SYSTEM, schema=PairsOut)
    except ai_gateway.AIUnavailable:
        return
    by_name = {m.entry.name.lower(): m for m in open_entries}
    valid = {c for c, _ in open_courses}
    for p in ai_gateway.as_list(data, "pairs"):
        if not isinstance(p, dict):
            continue
        m, code = by_name.get(str(p.get("roadmap", "")).strip().lower()), str(p.get("code", "")).strip().upper()
        if m and not m.course_code and code in valid:
            m.course_code, m.score, m.by = code, 0.0, "ai"
            valid.discard(code)


def save(db, user, version, matches: list[Match], filename: str | None) -> None:
    """Replace the version's roadmap with these rows and apply the semesters to its courses."""
    from sqlalchemy import delete

    from ..core.audit import record
    from ..models.curriculum import RoadmapEntry

    db.execute(delete(RoadmapEntry).where(RoadmapEntry.curriculum_version_id == version.id))
    for m in matches:
        e = m.entry
        db.add(RoadmapEntry(curriculum_version_id=version.id, semester=e.semester, position=e.position, name=e.name[:300],
                            credits=e.credits, credits_text=e.credits_text, course_code=m.course_code,
                            match_score=m.score if m.course_code else None, matched_by=m.by, source_filename=filename))
    db.flush()
    apply(db, version)
    record(db, user, "curriculum.roadmap_saved", "curriculum_version", version.id,
           {"filename": filename, "entries": len(matches), "matched": sum(1 for m in matches if m.course_code)})


def apply(db, version) -> None:
    """Course.semester (and the parsed outline) follow the roadmap: matched courses get their semester, the rest None."""
    from sqlalchemy import select

    from ..models.curriculum import RoadmapEntry
    from ..models.outcomes import Course

    rows = db.scalars(select(RoadmapEntry).where(RoadmapEntry.curriculum_version_id == version.id)).all()
    if not rows:
        return
    sem = {r.course_code: r.semester for r in rows if r.course_code}
    for c in db.scalars(select(Course).where(Course.curriculum_version_id == version.id)):
        c.semester = sem.get(c.code)
    parsed = dict(version.parsed or {})
    parsed["courses"] = [{**c, "semester": sem.get(c.get("code"))} for c in parsed.get("courses", [])]
    version.parsed = parsed


def out(db, version) -> dict:
    from sqlalchemy import select

    from ..models.curriculum import RoadmapEntry

    rows = db.scalars(select(RoadmapEntry).where(RoadmapEntry.curriculum_version_id == version.id)
                      .order_by(RoadmapEntry.semester, RoadmapEntry.position)).all()
    courses = version_courses(db, version)
    matched = {r.course_code for r in rows if r.course_code}
    return {
        "version_id": version.id, "filename": rows[0].source_filename if rows else None,
        "entries": [{"id": r.id, "semester": r.semester, "name": r.name, "credits": r.credits, "credits_text": r.credits_text,
                     "course_code": r.course_code, "match_score": r.match_score, "matched_by": r.matched_by} for r in rows],
        "courses": [{"code": c, "title": t} for c, t in sorted(courses)],
        "summary": {"entries": len(rows), "semesters": len({r.semester for r in rows}), "matched": len(matched),
                    "roadmap_only": sum(1 for r in rows if not r.course_code),
                    "courses_without_semester": sorted(c for c, _ in courses if c not in matched)},
    }


def import_file(db, user, version, filename: str, data: bytes) -> dict:
    """Read a roadmap file, match it to the version's courses (names, then AI) and save it."""
    result = read(filename, data, ai=ai_reader(db, user))
    return import_entries(db, user, version, result.entries, filename, result.engine)


def import_entries(db, user, version, entries: list[Entry], source: str, engine: str) -> dict:
    """Match roadmap entries to the version's courses (names, then AI) and save them."""
    courses = version_courses(db, version)
    matches = match(entries, courses)
    ai_pair(db, user, matches, courses)
    save(db, user, version, matches, source)
    return {**out(db, version), "engine": engine}
