"""Automation mode: PDF/outline -> AI extraction -> approved -> mapped -> active -> questions drafted by the AI
(a teacher must approve every test question) -> diagnostic live once enough are approved -> personalised lectures
per student."""
import json
import re

import pytest

from app.config import get_settings
from app.services import llm

from .helpers import SAMPLE_MD, as_role, login, make_user, setup_offering, upload_curriculum


@pytest.fixture()
def auto():
    s = get_settings()
    old = s.automation
    s.automation = True
    yield
    s.automation = old


def fake_ai(monkeypatch):
    """A stand-in model: items, mappings and personal lectures; anything else is not JSON (parser falls back)."""
    calls = []

    def gen(prompt, system=None, **kw):
        sysl = (system or "").lower()
        calls.append((sysl, prompt))
        if "multiple-choice" in sysl:
            items = [{"stem": f"Question number {i} about this outcome?", "options": [f"right {i}", f"wrong a{i}", f"wrong b{i}", f"wrong c{i}"],
                      "answer_index": 0, "explanation": "Because it follows from the definition.", "hints": ["Think about the definition."],
                      "difficulty": i % 3 + 1} for i in range(12)]
            if "QUESTION SET FOR" in prompt:  # batched: every question type for one CLO in one reply
                written = [{"type": t, "stem": f"Written {t} question {i} about this outcome _____?", "answer": "true",
                            "key_points": ["a point"], "explanation": "Because."}
                           for i in range(3) for t in ("true_false", "fill_blank", "short_answer")]
                text = json.dumps({**{p: [dict(x, stem=f"{p} {x['stem']}") for x in items]
                                      for p in ("diagnostic", "formative", "reteach")}, "written": written})
            else:
                text = json.dumps({"items": items})
        elif "curriculum design" in sysl:
            clos = re.findall(r"^(CLO\d+):", prompt, re.M)
            plo = re.search(r"^(PLO\d+):", prompt, re.M).group(1)
            text = json.dumps({"mappings": [{"clo": c, "plo": plo, "strength": 2, "rationale": "test"} for c in clos]})
        elif "personal lecture" in sysl:
            text = json.dumps({"lecture_md": "## Key idea\n\n" + "This is explained step by step with an example. " * 6,
                               "notes_md": "- First note\n- Second note"})
        else:
            text = "not json"
        return llm.LLMResult(text=text, model="gemini-test", input_tokens=5, output_tokens=5, latency_ms=3)

    monkeypatch.setattr(llm, "generate", gen)
    return calls


def upload_and_enrol(client, code="CS101"):
    _, ah = as_role(client, "admin")
    v = upload_curriculum(client, ah)
    course = next(c for c in v["automation"]["courses"] if c["code"] == code)
    inst = make_user("instructor")
    section = setup_offering(client, ah, course["id"], inst)
    student = make_user("student")
    sh = login(client, student.email)
    r = client.post("/api/enrollment", headers=sh, json={"section_id": section["id"]})
    assert r.status_code == 201, r.text
    return {"ah": ah, "ih": login(client, inst.email), "sh": sh, "v": v, "course": course, "student": student}


def test_upload_runs_the_whole_pipeline(client, auto, monkeypatch):
    calls = fake_ai(monkeypatch)
    _, ah = as_role(client, "admin")
    v = upload_curriculum(client, ah)
    a = v["automation"]
    assert a["stopped"] is None and a["status"] == "active", a
    # questions are batched: one call per CLO for all three types, no per-type top-up calls were needed
    # CLO-PLO links stated in the document are used as they are: only MATH110 (no links in the document) asks the AI
    link_calls = [p for s, p in calls if "curriculum design" in s]
    assert len(link_calls) == 1 and "MATH110" in link_calls[0], [p[:80] for p in link_calls]
    question_calls = [p for s, p in calls if "multiple-choice" in s]
    n_clos = sum(len(c["clos"]) for c in v["parsed"]["courses"])
    assert len(question_calls) == n_clos and all("QUESTION SET FOR" in p for p in question_calls)
    assert all(c["status"] == "active" for c in a["courses"])
    # questions were drafted from the CLOs but wait for a teacher: test questions always need approval
    cs = next(c for c in a["courses"] if c["code"] == "CS101")
    inst = make_user("instructor")  # the teacher who reviews questions is the course's instructor, not the admin
    setup_offering(client, ah, cs["id"], inst)
    ih = login(client, inst.email)
    cov = client.get(f"/api/items/coverage/{cs['id']}", headers=ah).json()
    assert not cov["published"]
    items = client.get(f"/api/items?course_id={cs['id']}&purpose=diagnostic", headers=ah).json()
    assert items and all(i["status"] == "pending" and i["source_chunk_ids"] == [] for i in items)
    assert client.get("/api/reviews?kind=diagnostic_item", headers=ih).json()
    assert all(i["status"] == "pending" for i in client.get(f"/api/items?course_id={cs['id']}&purpose=formative", headers=ah).json())
    # once the teacher has approved enough questions for every CLO, the diagnostic goes live by itself
    for it in items:
        assert client.post(f"/api/items/{it['id']}/review", headers=ih, json={"decision": "approve"}).status_code == 200
    cov = client.get(f"/api/items/coverage/{cs['id']}", headers=ah).json()
    assert cov["complete"] and cov["published"]
    # every automatic decision is audited
    events = client.get("/api/audit?action=outcomes.approved", headers=ah).json()
    assert events["total"] >= 1


def test_pipeline_stops_on_extraction_errors(client, auto):
    _, ah = as_role(client, "admin")
    bad = b"# Notes\n\nThis document has some text about computing but no programme, outcomes or courses at all.\n"
    r = client.post("/api/curriculum/upload", headers=ah, files={"file": ("notes.md", bad)})
    assert r.status_code == 201, r.text
    a = r.json()["automation"]
    assert a["status"] == "in_review" and a["stopped"] and a["errors"] and a["courses"] == []


def test_diagnostic_is_prepared_on_demand(client, auto, monkeypatch):
    c = upload_and_enrol(client)  # offline: no questions could be written at upload
    st = client.get(f"/api/diagnostic/{c['course']['id']}/status", headers=c["sh"]).json()
    assert st["published"] is False
    fake_ai(monkeypatch)  # the AI is back: the student's visit triggers drafting of the questions
    st = client.get(f"/api/diagnostic/{c['course']['id']}/status", headers=c["sh"]).json()
    assert st["published"] is False  # drafted, but not live until a teacher approves them
    pending = client.get(f"/api/items?course_id={c['course']['id']}&purpose=diagnostic&status=pending", headers=c["ih"]).json()
    assert pending
    for it in pending:
        client.post(f"/api/items/{it['id']}/review", headers=c["ih"], json={"decision": "approve"})
    st = client.get(f"/api/diagnostic/{c['course']['id']}/status", headers=c["sh"]).json()
    assert st["published"] is True
    r = client.post(f"/api/diagnostic/{c['course']['id']}/start", headers=c["sh"])
    assert r.status_code == 200 and r.json()["question"]


def test_personal_lecture_per_student_and_level(client, auto, monkeypatch):
    calls = fake_ai(monkeypatch)
    c = upload_and_enrol(client)
    clo = client.get(f"/api/outcomes/courses/{c['course']['id']}", headers=c["sh"]).json()["clos"][0]
    url = f"/api/learn/{c['course']['id']}/clos/{clo['id']}/lesson"
    first = client.get(url, headers=c["sh"]).json()
    assert first["origin"] == "ai" and first["level"] == "standard" and "Key idea" in first["lecture_md"] and first["notes_md"]
    prompt = next(p for s, p in calls if "personal lecture" in s)
    assert "LEARNER PROFILE" in prompt and "COURSE OUTLINE" in prompt and clo["code"] in prompt
    n = len(calls)
    again = client.get(url, headers=c["sh"]).json()
    assert again["ai_call_id"] == first["ai_call_id"] and len(calls) == n  # stored: stable, no new AI call
    # another student gets their own lesson
    other = make_user("student")
    oh = login(client, other.email)
    sec = client.get(f"/api/offerings/sections?course_id={c['course']['id']}", headers=c["ah"]).json()[0]
    client.post("/api/enrollment", headers=oh, json={"section_id": sec["id"]})
    mine = client.get(url, headers=oh).json()
    assert mine["origin"] == "ai" and mine["ai_call_id"] != first["ai_call_id"]
    # a learner can report the lecture: it goes to the review queue
    assert client.post(f"/api/ai/calls/{first['ai_call_id']}/flag", headers=c["sh"], json={"reason": "Example is wrong"}).status_code == 200


def test_level_follows_the_diagnostic(client, auto, monkeypatch):
    from app.db import SessionLocal
    from app.models.outcomes import CLO
    from app.services import personal_lessons

    fake_ai(monkeypatch)
    c = upload_and_enrol(client)
    db = SessionLocal()
    clo = db.query(CLO).filter(CLO.course_id == c["course"]["id"]).first()

    class R:  # a readiness result
        def __init__(self, level, pct):
            self.level, self.score_pct = level, pct
    monkeypatch.setattr(personal_lessons, "course_readiness", lambda *_: ({clo.id: R("not_ready", 20)}, {"source": "diagnostic"}))
    assert personal_lessons.level_for(db, c["student"].id, c["course"]["id"], clo)[0] == "foundations"
    monkeypatch.setattr(personal_lessons, "course_readiness", lambda *_: ({clo.id: R("ready", 90)}, {"source": "diagnostic"}))
    assert personal_lessons.level_for(db, c["student"].id, c["course"]["id"], clo)[0] == "extension"
    db.close()


def test_offline_lecture_is_a_study_note_and_not_stored(client, auto):
    c = upload_and_enrol(client)
    clo = client.get(f"/api/outcomes/courses/{c['course']['id']}", headers=c["sh"]).json()["clos"][0]
    r = client.get(f"/api/learn/{c['course']['id']}/clos/{clo['id']}/lesson", headers=c["sh"]).json()
    assert r["origin"] == "offline" and r["ai_call_id"] is None and clo["description"] in r["lecture_md"]


def test_personal_lectures_off_without_automation(client):
    from .helpers import active_course_with_student

    c = active_course_with_student(client)
    clo = c["course"]["clos"][0]
    r = client.get(f"/api/learn/{c['course']['id']}/clos/{clo['id']}/lesson", headers=c["sh"])
    assert r.status_code == 404


def test_two_courses_with_one_code_do_not_break_the_pipeline(client, auto, monkeypatch):
    """A document that reuses a course code: identical issues are listed once, and automation stops for a person
    (the code must be fixed in review - see test_review_fixes) instead of failing."""
    from .helpers import SAMPLE_MD

    fake_ai(monkeypatch)
    _, ah = as_role(client, "admin")
    text = SAMPLE_MD.decode()
    extra = text[text.index("## Course: CS201"):text.index("## Course: MATH110")].replace(
        "CS201 - Data Structures", "CS201 - Graph Theory")
    r = client.post("/api/curriculum/upload", headers=ah, files={"file": ("dup.md", (text + "\n\n" + extra).encode())})
    assert r.status_code == 201, r.text
    v = r.json()
    a = v["automation"]
    assert a["status"] == "in_review" and a["stopped"], a
    from app.db import SessionLocal
    from app.routers.outcomes import issues_with_acks

    db = SessionLocal()
    keys = [i["key"] for i in issues_with_acks(db, v["id"])]
    db.close()
    assert keys and len(keys) == len(set(keys))
