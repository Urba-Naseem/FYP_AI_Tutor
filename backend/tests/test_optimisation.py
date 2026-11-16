"""LLM optimisation: prompt compression, chunked parallel extraction, strict schemas, guardrails, batched questions."""
import json
import re

import pytest

from app.services import ai_gateway, llm, text_prep
from app.services import curriculum_parser as cp
from app.services.schemas import CurriculumOut, ItemBundleOut, ItemsOut, MappingsOut

from .helpers import as_role

# ---- 1. compression and splitting -------------------------------------------------------------------------

DOC = """# BSCS Programme

PLO1: Apply knowledge of computing fundamentals.
PLO2: Analyse complex computing problems.

<!-- image -->

| Page 1 of 3 |
|---|

## CS101: Programming Fundamentals

| CLO | Description | Bloom | Weight |
|-----|-------------|-------|--------|
| CLO1 | Explain the basic structure of a program. | L2 - Understanding | 40% |
| CLO2 | Write programs that use loops and functions. | L3 - Applying | 60% |

| Page 2 of 3 |
|---|

## CS201: Data Structures

| CLO | Description | Bloom | Weight |
|-----|-------------|-------|--------|
| CLO1 | Describe arrays, lists and trees. | L2 - Understanding | 50% |
| CLO2 | Implement a stack and a queue. | L3 - Applying | 45% |
"""


def test_compress_keeps_content_and_drops_padding():
    out = text_prep.compress(DOC)
    assert len(out) < len(DOC)
    assert "<!-- image -->" not in out and "|-----|" not in out
    for must in ("PLO1", "Explain the basic structure of a program.", "Implement a stack and a queue.", "60%"):
        assert must in out


def test_split_courses_puts_plos_in_head_and_one_chunk_per_course():
    head, chunks = text_prep.split_courses(text_prep.compress(DOC))
    assert "PLO1" in head and "CS101" not in head
    assert len(chunks) == 2 and chunks[0].lstrip("# ").startswith("CS101") and "CS201" in chunks[1]
    assert len(text_prep.group(chunks, 1)) == 1 and len(text_prep.group(chunks, 5)) == 2


def test_plo_codes_are_read_without_ai():
    assert cp.plo_codes(DOC) == ["PLO1", "PLO2"]


# ---- 3. strict schemas -----------------------------------------------------------------------------------


def test_schema_of_is_gemini_ready():
    s = ai_gateway.schema_of(CurriculumOut)
    text = json.dumps(s)
    assert "$ref" not in text and "$defs" not in text
    course = s["properties"]["courses"]["items"]
    assert "title" in course["properties"]  # a field called "title" is kept; schema titles are dropped
    assert "title" not in course
    assert course["properties"]["credits"].get("nullable") is True


def test_validated_drops_bad_entries_but_keeps_good_ones():
    good = {"stem": "Which loop runs at least once?", "options": ["do-while", "for", "while", "none"],
            "answer_index": 0, "explanation": "It tests after the body.", "hints": [], "difficulty": 2}
    bad = dict(good, options=["a", "b", "c"])  # only 3 options
    assert len(ai_gateway._validated(ItemsOut, {"items": [good, bad]})["items"]) == 1
    assert len(ai_gateway._validated(ItemsOut, [good])["items"]) == 1  # bare list accepted
    with pytest.raises(ValueError):
        ai_gateway._validated(ItemsOut, {"items": [bad]})  # nothing usable is still an error
    with pytest.raises(ValueError):
        ai_gateway._validated(MappingsOut, {"mappings": [{"clo": "CLO1", "plo": "PLO1", "strength": 7, "rationale": "x"}]})


# ---- 4. guardrails -----------------------------------------------------------------------------------------


def parsed(weights=(40, 60), clo2="Write programs that use loops and functions.", plo="PLO1"):
    return cp.normalise({"program": {"code": "BSCS", "name": "BSCS"},
                         "plos": [{"code": "PLO1", "description": "Apply knowledge of computing fundamentals."}],
                         "courses": [{"code": "CS101", "title": "Programming Fundamentals", "credits": 3, "units": [],
                                      "clos": [{"code": "CLO1", "description": "Explain the basic structure of a program.",
                                                "bloom": "L2 - Understanding", "weight": weights[0]},
                                               {"code": "CLO2", "description": clo2, "bloom": "L3 - Applying",
                                                "weight": weights[1]}],
                                      "clo_plo": [{"clo": "CLO1", "plo": plo, "strength": 2}]}]})


def messages(p, source=DOC, ai_wording=None):
    return [i["message"] for i in cp.guard(p, source, ai_wording)]


def test_guard_passes_a_correct_extraction():
    assert messages(parsed()) == []


def test_guard_flags_weights_that_do_not_total_100():
    assert any("total 95%" in m for m in messages(parsed(weights=(40, 55))))


def test_guard_flags_invented_wording_and_unknown_plos():
    m = messages(parsed(clo2="Design distributed blockchain consensus protocols for satellites.", plo="PLO9"))
    assert any("CLO2 is not worded like anything" in x for x in m)
    assert any("PLO9" in x for x in m)


def test_guard_trusts_wording_a_person_edited():
    p = parsed(clo2="Design distributed blockchain consensus protocols for satellites.")
    ai = {"Explain the basic structure of a program.", "Write programs that use loops and functions."}
    assert not any("not worded like" in x for x in messages(p, ai_wording=ai))


@pytest.mark.parametrize("raw,level", [("Cognitive, L3 – Applying", "apply"), ("L2 - Understanding", "understand"),
                                       ("analyse", "analyze"), (None, None)])
def test_bloom_level_normalised(raw, level):
    assert cp.bloom_level(raw) == level


# ---- 1+2. chunked, parallel extraction -----------------------------------------------------------------------


@pytest.fixture()
def small_parts(monkeypatch):
    """Test documents are short: read them in small parts so the multi-part path is exercised."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "curriculum_part_chars", 400)


def test_curriculum_is_read_in_parallel_parts(client, monkeypatch, small_parts):
    calls = []

    def gen(prompt, system=None, schema=None, temperature=None, **kw):
        calls.append({"prompt": prompt, "schema": schema, "temperature": temperature})
        if "PROGRAMME PLO CODES" in prompt:
            courses = []
            for code, title in re.findall(r"(CS\d{3}): ([A-Za-z ]+)", prompt):
                clos = re.findall(r"\| (CLO\d) \| ([^|]+) \| ([^|]+) \| (\d+)%", prompt.split(code, 1)[1].split("CS", 1)[0])
                courses.append({"code": code, "title": title.strip(), "credits": 3, "units": [], "prerequisites": [],
                                "clos": [{"code": c, "description": d.strip(), "bloom": b.strip(), "weight": float(w),
                                          "plos": ["PLO1"]} for c, d, b, w in clos]})
            text = json.dumps({"courses": courses})
        else:
            text = json.dumps({"program": {"code": "BSCS", "name": "BSCS"},
                               "plos": [{"code": "PLO1", "description": "Apply knowledge of computing fundamentals."},
                                        {"code": "PLO2", "description": "Analyse complex computing problems."}]})
        return llm.LLMResult(text=text, model="gemini-test", input_tokens=5, output_tokens=5, latency_ms=3)

    monkeypatch.setattr(llm, "generate", gen)
    _, ah = as_role(client, "admin")
    r = client.post("/api/curriculum/upload", headers=ah, files={"file": ("bscs.md", DOC.encode(), "text/markdown")})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["parser"] == "gemini"
    assert len(calls) >= 2  # programme/PLOs and the course parts are separate calls
    assert all(c["schema"] and c["temperature"] == 0.0 for c in calls)  # strict schema, deterministic
    assert all(c["prompt"].startswith("PROGRAMME PLO CODES: PLO1, PLO2") for c in calls if "CS101" in c["prompt"])
    codes = sorted(c["code"] for c in body["parsed"]["courses"])
    assert codes == ["CS101", "CS201"]
    warnings = [i["message"] for i in body["warnings"]]
    assert any("CS201 CLO weights total 95%" in w for w in warnings)  # guardrail reaches the reviewer


# ---- batched questions --------------------------------------------------------------------------------------


def test_bundle_items_dedupes_and_caps():
    item = {"stem": "Which loop runs at least once?", "options": ["do-while", "for", "while", "none"],
            "answer_index": 0, "explanation": "x", "hints": ["Think about when the test runs."], "difficulty": 2}
    data = ai_gateway._validated(ItemBundleOut, {"diagnostic": [item, item], "formative": [item, dict(item, stem="Another q?? yes")]})
    out = __import__("app.services.items", fromlist=["x"]).bundle_items(data, {"diagnostic": 1, "formative": 5, "reteach": 2}, [7])
    assert len(out["diagnostic"]) == 1 and len(out["formative"]) == 1 and out["reteach"] == []
    assert out["diagnostic"][0]["chunk_ids"] == [7]


def test_upload_reports_progress(client, monkeypatch, small_parts):
    """The page polls /upload-progress/{id}: course total from the document, courses counted as parts return."""
    seen = []
    from app.services import progress

    real_add = progress.add_courses

    def spy(run_id, n):
        real_add(run_id, n)
        seen.append(progress.get(run_id, uid)["courses_done"])
    monkeypatch.setattr(progress, "add_courses", spy)

    def gen(prompt, system=None, **kw):
        if "PROGRAMME PLO CODES" in prompt:
            codes = re.findall(r"(CS\d{3}): ([A-Za-z ]+)", prompt)
            text = json.dumps({"courses": [{"code": c, "title": t.strip(), "clos": [
                {"code": "CLO1", "description": "Explain the basic structure of a program."}]} for c, t in codes]})
        else:
            text = json.dumps({"program": {"code": "BSCS", "name": "BSCS"},
                               "plos": [{"code": "PLO1", "description": "Apply knowledge of computing fundamentals."}]})
        return llm.LLMResult(text=text, model="gemini-test", input_tokens=5, output_tokens=5, latency_ms=3)

    monkeypatch.setattr(llm, "generate", gen)
    admin, ah = as_role(client, "admin")
    uid = admin.id
    r = client.post("/api/curriculum/upload", headers=ah, data={"upload_id": "test-run-0001"},
                    files={"file": ("bscs.md", DOC.encode(), "text/markdown")})
    assert r.status_code == 202 and r.json()["upload_id"] == "test-run-0001", r.text  # answered at once: background job
    p = client.get("/api/curriculum/upload-progress/test-run-0001", headers=ah).json()
    assert p["done"] and p["stage"] == "done" and p["courses_total"] == 2 and p["courses_done"] == 2
    assert p["error"] is None and p["result"]["parser"] == "gemini" and p["result"]["summary"]["courses"] == 2
    assert seen and seen[-1] == 2  # counted up while the parts came back
    _, other = as_role(client, "admin")
    assert client.get("/api/curriculum/upload-progress/test-run-0001", headers=other).status_code == 404


def test_upload_errors_are_reported_through_progress(client, monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("down")))
    _, ah = as_role(client, "admin")
    tiny = b"# Notes\n\nx\n"
    r = client.post("/api/curriculum/upload", headers=ah, data={"upload_id": "test-run-0002"},
                    files={"file": ("notes.md", tiny, "text/markdown")})
    assert r.status_code == 202
    p = client.get("/api/curriculum/upload-progress/test-run-0002", headers=ah).json()
    assert p["done"] and p["stage"] == "failed" and "Almost no text" in p["error"] and p["result"] is None


def many_courses(n):
    return "PLO1: Apply knowledge of computing fundamentals.\n\n" + "\n\n".join(
        f"## CS{100 + i}: Course number {i}\n\nCLO1: Explain topic {i} clearly and correctly." for i in range(n))


def test_a_failed_part_is_retried_and_nothing_is_thrown_away(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "curriculum_part_chars", 250)
    calls = []

    def gen(prompt, system=None, **kw):
        body = prompt.split("DOCUMENT PART:", 1)[-1]  # a real model is told not to extract from the context line
        codes = re.findall(r"## (CS\d{3}): (Course number \d+)", body)
        if "PROGRAMME PLO CODES" in prompt:
            calls.append([c for c, _ in codes])
            if "CS100" in body and sum("CS100" in x for x in calls) == 1:  # its part times out once
                raise llm.LLMUnavailable("504 DEADLINE_EXCEEDED")
            text = json.dumps({"courses": [{"code": c, "title": t, "clos": [
                {"code": "CLO1", "description": f"Explain topic {t.split()[-1]} clearly and correctly."}]} for c, t in codes]})
        else:
            text = json.dumps({"program": {"code": "BSCS", "name": "BSCS"},
                               "plos": [{"code": "PLO1", "description": "Apply knowledge of computing fundamentals."}]})
        return llm.LLMResult(text=text, model="gemini-test", input_tokens=5, output_tokens=5, latency_ms=3)

    monkeypatch.setattr(llm, "generate", gen)
    _, ah = as_role(client, "admin")
    r = client.post("/api/curriculum/upload", headers=ah, files={"file": ("big.md", many_courses(12).encode(), "text/markdown")})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["parser"] == "gemini" and v["summary"]["courses"] == 12  # nothing thrown away
    assert len(calls) > 3 and max(len(x) for x in calls) <= 4, calls  # several small parts
    assert sum("CS100" in x for x in calls) == 2  # the failed part was read again


def test_part_that_fails_twice_is_named(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "curriculum_part_chars", 90)  # about one course per part

    def gen(prompt, system=None, **kw):
        body = prompt.split("DOCUMENT PART:", 1)[-1]  # a real model is told not to extract from the context line
        codes = re.findall(r"## (CS\d{3}): (Course number \d+)", body)
        if "PROGRAMME PLO CODES" in prompt:
            if "CS103" in body:
                raise llm.LLMUnavailable("504 DEADLINE_EXCEEDED")
            text = json.dumps({"courses": [{"code": c, "title": t, "clos": [
                {"code": "CLO1", "description": f"Explain topic {t.split()[-1]} clearly and correctly."}]} for c, t in codes]})
        else:
            text = json.dumps({"program": {"code": "BSCS", "name": "BSCS"},
                               "plos": [{"code": "PLO1", "description": "Apply knowledge of computing fundamentals."}]})
        return llm.LLMResult(text=text, model="gemini-test", input_tokens=5, output_tokens=5, latency_ms=3)

    monkeypatch.setattr(llm, "generate", gen)
    _, ah = as_role(client, "admin")
    v = client.post("/api/curriculum/upload", headers=ah, files={"file": ("big.md", many_courses(8).encode(), "text/markdown")}).json()
    assert v["summary"]["courses"] == 7
    assert any("could not read 1 part" in w["message"] and "CS103" in w["message"] for w in v["warnings"])


def test_documents_without_code_headings_are_read_in_parts_and_merged(client, monkeypatch):
    """Another layout: 'Course Title:' fields, no codes in headings, a course cut across two parts."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "curriculum_part_chars", 300)
    prompts = []
    doc = ("Programme: Software Engineering\nOutcome PLO1: Apply engineering knowledge to software problems.\n\n"
           + "\n\n".join(f"Course Title: Topic {i}\nCourse Code: SE{200 + i}\nOutcomes:\n"
                         + "\n".join(f"{k}. Students can explain idea {k} of topic {i} with an example." for k in range(1, 5))
                         for i in range(3)))

    def gen(prompt, system=None, **kw):
        prompts.append(prompt)
        if "PROGRAMME PLO CODES" in prompt:
            part = prompt.split("DOCUMENT PART:", 1)[1]
            ctx = prompt.split("CONTEXT:", 1)[1].split("DOCUMENT PART:", 1)[0] if "CONTEXT:" in prompt else ""
            courses = []
            for i in range(3):
                here = [k for k in range(1, 5) if f"idea {k} of topic {i}" in part]
                if here and (f"Topic {i}" in part or f"Topic {i}" in ctx):
                    courses.append({"code": f"SE{200 + i}", "title": f"Topic {i}", "clos": [
                        {"code": f"CLO{k}", "description": f"Students can explain idea {k} of topic {i} with an example."}
                        for k in here]})
            text = json.dumps({"courses": courses})
        else:
            text = json.dumps({"program": {"code": "BSSE", "name": "Software Engineering"},
                               "plos": [{"code": "PLO1", "description": "Apply engineering knowledge to software problems."}]})
        return llm.LLMResult(text=text, model="gemini-test", input_tokens=5, output_tokens=5, latency_ms=3)

    monkeypatch.setattr(llm, "generate", gen)
    _, ah = as_role(client, "admin")
    v = client.post("/api/curriculum/upload", headers=ah, files={"file": ("bsse.md", doc.encode(), "text/markdown")}).json()
    assert v["parser"] == "gemini", v["warnings"]
    by_code = {c["code"]: c for c in v["parsed"]["courses"]}
    assert sorted(by_code) == ["SE200", "SE201", "SE202"]  # no duplicates from courses cut across parts
    assert all(len(c["clos"]) == 4 for c in by_code.values())
    assert any("CONTEXT: This part continues" in p for p in prompts)  # a cut course was named for the next part


def test_merge_keeps_different_courses_that_share_a_code():
    merged = text_prep.merge_courses([
        {"code": "SE3223", "title": "Human Computer Interaction", "clos": [{"code": "CLO1", "description": "a"}]},
        {"code": "SE3223", "title": "HCI and Computer Graphics", "clos": [{"code": "CLO1", "description": "b"}]},
        {"code": "SE3223", "title": "Human Computer Interaction", "clos": [{"code": "CLO2", "description": "c"}]},
    ])
    assert len(merged) == 2 and [len(c["clos"]) for c in merged] == [2, 1]


def test_one_code_for_two_courses_is_flagged():
    p = parsed()
    p["courses"].append(dict(p["courses"][0], title="Graph Theory"))
    msgs = [i["message"] for i in cp.check_structure(p)]
    assert any("CS101 is used for 2 courses" in m and "Graph Theory" in m for m in msgs)


# ---- fast PDF text --------------------------------------------------------------------------------------------


def test_running_headers_are_removed_but_repeated_table_rows_are_kept():
    from app.services.extraction import _drop_running_lines

    row = "Cognitive, L3 – Applying PLO 4 35%"
    pages = [f"Riphah University OBE v2.2 Page {i}\nCS10{i}: Course {i}\n1\nExplain topic {i}\n"
             + (row if i % 2 else f"Write report {i} Cognitive, L6 – Creating PLO {i} 20%") for i in range(1, 7)]
    out = _drop_running_lines(pages)
    assert all("Riphah University" not in p for p in out)  # on every page: a running header
    assert sum(p.rstrip().endswith(row) for p in out) == 3  # ends half the pages, but it is content: kept


def test_compress_keeps_repeated_clo_rows():
    text = "\n".join(f"CS10{i}: Course {i}\nCLO Statement Domain / Bloom's Level PLO Weight\n1\nExplain topic {i}\n"
                     f"Cognitive, L3 – Applying PLO 2 30%\nFaculty of Computing Page {i}" for i in range(1, 5))
    out = text_prep.compress(text)
    assert out.count("Cognitive, L3 – Applying PLO 2 30%") == 4 and out.count("\n1\n") >= 3
    assert "Page" not in out  # the page footer goes


def test_fast_pdf_reader_joins_cells_split_after_a_dash(monkeypatch):
    import types

    from app.services import extraction

    text = ("CS101: Programming Credit Hours: 3\n1\nShow care for ethical practice in teams and projects\nAffective, L2 �\n"
            "Responding PLO 7 35%\n2\nWrite programs using loops and functions Cognitive, L3 � Applying PLO 2 65%\n") * 3

    class Page:
        def get_textpage(self):
            return types.SimpleNamespace(get_text_range=lambda: text)

    fake = types.SimpleNamespace(PdfDocument=lambda data: [Page()])
    monkeypatch.setitem(__import__("sys").modules, "pypdfium2", fake)
    out = extraction.fast_pdf_text(b"%PDF-fake")
    assert "Affective, L2 – Responding PLO 7 35%" in out  # a cell split after a dash is joined back


def test_scanned_pdf_falls_back_to_docling(monkeypatch):
    import types

    from app.services import extraction

    class Page:
        def get_textpage(self):
            return types.SimpleNamespace(get_text_range=lambda: "  \n ")

    monkeypatch.setitem(__import__("sys").modules, "pypdfium2", types.SimpleNamespace(PdfDocument=lambda d: [Page(), Page()]))
    assert extraction.fast_pdf_text(b"%PDF-fake") is None  # no text layer: Docling (with its models) takes over
