"""Course topics (a semester of teaching), topic-level personal paths, topic lessons, instructor edits and AI
practice questions."""
import json
import re

from app.db import SessionLocal
from app.models.items import Item
from app.models.mastery import MasteryEvidence
from app.models.outcomes import Course
from app.models.topics import Topic
from app.services import llm, topic_planner

from .helpers import login, make_items, make_user, published_diagnostic, take_diagnostic


def fake(monkeypatch, plan=None):
    """Model stand-in: topic plans, topic lectures and practice questions."""
    calls = []

    def gen(prompt, system=None, **kw):
        sysl = (system or "").lower()
        calls.append((sysl, prompt))
        if "weekly teaching topics" in sysl:
            if plan is not None:
                return llm.LLMResult(json.dumps(plan), "gemini-test", 1, 1, 1)
            clos = re.findall(r"\((CLO\d+(?:, CLO\d+)*)\) must be served", prompt)[0].split(", ")
            n = int(re.search(r"Plan exactly (\d+) teaching topics", prompt).group(1))
            topics = [{"title": f"Week {i + 1} topic", "summary": "What this week teaches.", "clos": [clos[i % len(clos)]]} for i in range(n)]
            return llm.LLMResult(json.dumps({"topics": topics}), "gemini-test", 1, 1, 1)
        if "practice questions" in sysl:
            k = len(calls)
            qs = [{"stem": f"Practice question {k}-{i}?", "options": ["right", "wrong a", "wrong b", "wrong c"], "answer_index": 0,
                   "explanation": "Right follows from the definition."} for i in range(5)]
            return llm.LLMResult(json.dumps({"questions": qs}), "gemini-test", 1, 1, 1)
        if "personal lecture" in sysl:
            slides = [{"title": f"Slide {i}", "points": ["Key point", "Another point"],
                       "visual": "```mermaid\ngraph LR; A-->B\n```" if i % 3 == 0 else "",
                       "explanation": "What a lecturer would say about this slide. " * 3} for i in range(10)] + [{"title": "", "points": []}]
            return llm.LLMResult(json.dumps({"lecture_md": "## This topic\n\n" + "Explained step by step with an example. " * 6,
                                             "notes_md": "- note one\n- note two", "slides": slides}), "gemini-test", 1, 1, 1)
        return llm.LLMResult("not json", "gemini-test", 1, 1, 1)

    monkeypatch.setattr(llm, "generate", gen)
    return calls


def test_offline_plan_covers_every_clo_with_a_semester_of_topics(client):
    c = published_diagnostic(client)
    ts = client.get(f"/api/learn/{c['course']['id']}/topics", headers=c["sh"]).json()
    db = SessionLocal()
    course = db.get(Course, c["course"]["id"])
    want = topic_planner.topic_count(course, len(c["course"]["clos"]))
    db.close()
    assert len(ts) == want >= 12 and all(t["origin"] == "fallback" for t in ts)
    assert {cl["code"] for t in ts for cl in t["clos"]} == {o["code"] for o in c["course"]["clos"]}
    assert [t["number"] for t in ts] == list(range(1, len(ts) + 1))


def test_ai_plan_is_used_when_valid_and_rejected_when_it_drops_a_clo(client, monkeypatch):
    c = published_diagnostic(client)
    fake(monkeypatch)
    ts = client.get(f"/api/learn/{c['course']['id']}/topics", headers=c["sh"]).json()
    assert all(t["origin"] == "ai" for t in ts) and ts[0]["title"].startswith("Week")
    # a plan that leaves a CLO out falls back to the plain plan (another course of the same curriculum)
    fake(monkeypatch, plan={"topics": [{"title": "Only one", "summary": "x", "clos": ["CLO1"]}] * 12})
    db = SessionLocal()
    cs201 = db.get(Course, c["courses"]["CS201"]["id"])
    ts2 = topic_planner.ensure(db, c["inst"], cs201)
    assert ts2 and all(t.origin == "fallback" for t in ts2)
    db.rollback()
    db.close()


def test_two_students_with_different_diagnostics_get_different_paths(client):
    c = published_diagnostic(client)
    codes = [o["code"] for o in c["course"]["clos"]]
    take_diagnostic(client, c, correct_clos=codes[:2])  # strong on the first two CLOs
    p1 = client.get(f"/api/paths/me/{c['course']['id']}", headers=c["sh"]).json()
    other = make_user("student")
    sh2 = login(client, other.email)
    assert client.post("/api/enrollment", headers=sh2, json={"section_id": c["section"]["id"]}).status_code == 201
    take_diagnostic(client, {**c, "sh": sh2}, correct_clos=codes[2:])  # strong on the others
    p2 = client.get(f"/api/paths/me/{c['course']['id']}", headers=sh2).json()

    def shape(p):
        return [(s["clo_code"], s["step_type"]) for s in p["steps"]]
    assert shape(p1) != shape(p2)
    # weak CLOs are studied topic by topic; strong ones are a quick check only
    learn1 = [s for s in p1["steps"] if s["step_type"] == "learn"]
    assert learn1 and all(s["meta"].get("topic_id") and s["label"] == s["meta"]["topic_title"] for s in learn1)
    assert {s["clo_code"] for s in learn1}.isdisjoint(codes[:2])
    assert {s["clo_code"] for s in p1["steps"] if s["step_type"] == "review"} == set(codes[:2])
    assert "weakest outcome first" in p1["summary"]


def test_old_path_is_upgraded_to_topics_keeping_progress(client):
    c = published_diagnostic(client)
    db = SessionLocal()
    for t in db.query(Topic).filter(Topic.course_id == c["course"]["id"]).all():
        db.delete(t)
    db.commit()
    db.close()
    # a path built while the course had no topics (path made without topics, then topics removed for the test)
    r = client.post(f"/api/paths/me/{c['course']['id']}/generate", headers=c["sh"]).json()
    db = SessionLocal()
    for t in db.query(Topic).filter(Topic.course_id == c["course"]["id"]).all():
        db.delete(t)
    db.commit()
    from app.models.paths import PathStep

    first_learn = db.query(PathStep).filter(PathStep.path_id == r["id"], PathStep.step_type == "learn").first()
    db.query(PathStep).filter(PathStep.path_id == r["id"], PathStep.step_type == "learn", PathStep.meta.isnot(None)).update({"meta": {}}, synchronize_session=False)
    first_learn.status = "done"
    clo_done = first_learn.clo_id
    db.commit()
    db.close()
    p = client.get(f"/api/paths/me/{c['course']['id']}", headers=c["sh"]).json()
    assert p["version_no"] == r["version_no"] + 1 and p["reason"] == "Course topics added"
    topic_steps = [s for s in p["steps"] if s["meta"].get("topic_id")]
    assert topic_steps and all(s["status"] == "done" for s in topic_steps if s["clo_id"] == clo_done)


def test_topic_lesson_is_personal_and_instructor_can_edit_or_rewrite(client, monkeypatch):
    c = published_diagnostic(client)
    calls = fake(monkeypatch)
    take_diagnostic(client, c)
    ts = client.get(f"/api/learn/{c['course']['id']}/topics", headers=c["sh"]).json()
    t = ts[1]
    les = client.get(f"/api/learn/{c['course']['id']}/topics/{t['id']}/lesson", headers=c["sh"]).json()
    assert les["origin"] == "ai" and les["topic_id"] == t["id"] and les["previous_topic"]["id"] == ts[0]["id"]
    assert len(les["slides"]) == 10 and les["slides"][0]["visual"].startswith("```mermaid")  # the empty slide was dropped
    lecture_prompt = [p for s, p in calls if "personal lecture" in s][-1]
    assert f"TOPIC {t['number']} of {len(ts)}: {t['title']}" in lecture_prompt and "LEARNER PROFILE" in lecture_prompt
    # cached: a second visit does not call the AI again
    n = len(calls)
    client.get(f"/api/learn/{c['course']['id']}/topics/{t['id']}/lesson", headers=c["sh"])
    assert len(calls) == n
    # the instructor sees the student's lessons, edits one, and has one rewritten with a note
    lst = client.get(f"/api/topics/course/{c['course']['id']}/student/{c['student'].id}/lessons", headers=c["ih"]).json()
    lid = next(x["id"] for x in lst if x["topic_id"] == t["id"])
    e = client.put(f"/api/topics/lessons/{lid}", headers=c["ih"], json={"lecture_md": "## Rewritten by hand\n\n" + "Clearer text. " * 5, "notes_md": "- n"})
    assert e.status_code == 200 and e.json()["edited"] is True
    edited = client.get(f"/api/learn/{c['course']['id']}/topics/{t['id']}/lesson", headers=c["sh"]).json()
    assert "Rewritten by hand" in edited["lecture_md"] and edited["slides"] == []  # old slides no longer match the text
    rw = client.post(f"/api/topics/lessons/{lid}/rewrite", headers=c["ih"], json={"note": "Use a cooking analogy"})
    assert rw.status_code == 200 and rw.json()["instructor_note"] == "Use a cooking analogy"
    assert "INSTRUCTOR NOTE (follow it): Use a cooking analogy" in [p for s, p in calls if "personal lecture" in s][-1]
    # students cannot use the instructor endpoints; admins cannot edit (teaching work)
    assert client.put(f"/api/topics/lessons/{lid}", headers=c["sh"], json={"lecture_md": "x" * 50}).status_code == 403
    assert client.put(f"/api/topics/lessons/{lid}", headers=c["ah"], json={"lecture_md": "x" * 50}).status_code == 403


def test_instructor_edits_hides_and_adds_topics(client):
    c = published_diagnostic(client)
    cid = c["course"]["id"]
    d = client.get(f"/api/topics/course/{cid}", headers=c["ih"]).json()
    t = d["topics"][0]
    r = client.patch(f"/api/topics/{t['id']}", headers=c["ih"], json={"title": "Variables in depth", "status": "hidden"})
    assert r.status_code == 200 and r.json()["status"] == "hidden"
    assert all(x["id"] != t["id"] for x in client.get(f"/api/learn/{cid}/topics", headers=c["sh"]).json())
    clo = c["course"]["clos"][0]["id"]
    a = client.post(f"/api/topics/course/{cid}", headers=c["ih"], json={"title": "Extra revision", "summary": "Exam prep", "clo_ids": [clo]})
    assert a.status_code == 201 and a.json()["number"] == len(d["topics"]) + 1
    assert client.post(f"/api/topics/course/{cid}", headers=c["ih"], json={"title": "Bad", "clo_ids": [999999]}).status_code == 422
    events = {e["action"] for e in client.get("/api/audit?action=topics", headers=c["ah"]).json()["items"]}
    assert {"topics.planned", "topics.edited", "topics.added"} <= events


def test_practice_is_ungraded_separate_from_the_bank_and_never_repeats(client, monkeypatch):
    c = published_diagnostic(client)
    cid, clo = c["course"]["id"], c["course"]["clos"][0]["id"]
    # no AI: a friendly 503, nothing stored
    r = client.post(f"/api/practice/{cid}/clos/{clo}", headers=c["sh"], json={})
    assert r.status_code == 503 and "try again" in r.json()["detail"]
    calls = fake(monkeypatch)
    db = SessionLocal()
    items_before = db.query(Item).count()
    db.close()
    s1 = client.post(f"/api/practice/{cid}/clos/{clo}", headers=c["sh"], json={}).json()
    assert s1["total"] == 5 and s1["counts_toward_mastery"] is False
    assert all("correct_index" not in q and "explanation" not in q for q in s1["questions"])  # answers hidden until answered
    q0 = s1["questions"][0]
    right = q0["options"].index("right")
    a = client.post(f"/api/practice/sets/{s1['id']}/answer", headers=c["sh"], json={"index": 0, "choice": right}).json()
    assert a["questions"][0]["correct"] is True and a["questions"][0]["explanation"]
    # a second set tells the AI which stems to avoid; practice never adds bank items or mastery evidence
    client.post(f"/api/practice/{cid}/clos/{clo}", headers=c["sh"], json={})
    assert q0["stem"] in [p for s, p in calls if "practice questions" in s][-1]
    db = SessionLocal()
    assert db.query(Item).count() == items_before and db.query(MasteryEvidence).count() == 0
    db.close()
    # another student cannot open this set
    other = make_user("student")
    assert client.get(f"/api/practice/sets/{s1['id']}", headers=login(client, other.email)).status_code == 404


def test_formative_items_unaffected_by_topics(client):
    """Topics add study steps; formative quizzes still come from the approved bank only."""
    c = published_diagnostic(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=6)
    take_diagnostic(client, c)
    p = client.get(f"/api/paths/me/{c['course']['id']}", headers=c["sh"]).json()
    assert any(s["step_type"] in ("practice", "review") for s in p["steps"])


def test_lessons_practice_and_quizzes_follow_each_students_diagnostic(client, monkeypatch):
    """Two students in the same course: one passes CLO1 on the diagnostic, the other fails it. Their CLO1 lesson,
    practice questions and formative quiz must all differ - nothing is the same 'one size' content."""
    c = published_diagnostic(client, per_clo=9)
    cid, clo1 = c["course"]["id"], c["course"]["clos"][0]
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=15)  # 5 easy, 5 medium, 5 hard per CLO
    calls = fake(monkeypatch)
    take_diagnostic(client, c, correct_clos={clo1["code"]})  # strong on CLO1
    weak = make_user("student")
    wh = login(client, weak.email)
    assert client.post("/api/enrollment", headers=wh, json={"section_id": c["section"]["id"]}).status_code == 201
    take_diagnostic(client, {**c, "sh": wh})  # weak on everything

    topic = next(t for t in client.get(f"/api/learn/{cid}/topics", headers=c["sh"]).json() if t["clos"][0]["id"] == clo1["id"])
    strong_lesson = client.get(f"/api/learn/{cid}/topics/{topic['id']}/lesson", headers=c["sh"]).json()
    strong_prompt = [p for s, p in calls if "personal lecture" in s][-1]
    weak_lesson = client.get(f"/api/learn/{cid}/topics/{topic['id']}/lesson", headers=wh).json()
    weak_prompt = [p for s, p in calls if "personal lecture" in s][-1]
    # lesson: pitched from each diagnostic (different level, different teaching instructions to the AI)
    assert strong_lesson["level"] == "extension" and weak_lesson["level"] == "foundations"
    assert "is READY for this outcome" in strong_prompt and "NOT READY for this outcome" in weak_prompt
    assert strong_lesson["id"] != weak_lesson["id"]  # each student has their own stored lesson

    # practice: the AI is told each student's level
    client.post(f"/api/practice/{cid}/clos/{clo1['id']}", headers=c["sh"], json={})
    assert "LEARNER LEVEL: Stretch" in [p for s, p in calls if "practice questions" in s][-1]
    client.post(f"/api/practice/{cid}/clos/{clo1['id']}", headers=wh, json={})
    assert "LEARNER LEVEL: Foundations" in [p for s, p in calls if "practice questions" in s][-1]

    # formative quiz: harder questions for the strong student, easier for the weak one, different question sets
    qs = {}
    from .helpers import walk_to_quiz

    for name, h in (("strong", c["sh"]), ("weak", wh)):
        walk_to_quiz(client, h, cid, clo1["id"])
        a = client.post("/api/quiz/start", headers=h, json={"course_id": cid, "clo_id": clo1["id"]}).json()
        db = SessionLocal()
        qs[name] = [db.get(Item, q["id"]) for q in a["questions"]]
        db.close()
    avg = {k: sum(i.difficulty for i in v) / len(v) for k, v in qs.items()}
    assert avg["strong"] > avg["weak"], avg
    assert {i.id for i in qs["strong"]} != {i.id for i in qs["weak"]}
