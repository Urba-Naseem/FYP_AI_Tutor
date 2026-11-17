"""After a quiz the AI talks through the wrong answers like a teacher (asks why, diagnoses, re-explains), and what it
learns shapes the reteach explanation. Also: practice hints / one set per lecture, and Socratic nudging in the tutors."""
import json

from app.services import llm

from .helpers import active_course_with_student, make_items, published_diagnostic, right, take_diagnostic, take_quiz, wrong


def ctx(client, n_correct=7):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=10)
    c["clo"] = c["course"]["clos"][0]
    c["quiz"] = take_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"], n_correct, out_of=10)
    return c


def teacher(monkeypatch, replies):
    """Fake Gemini for the debrief: returns the given teacher turns in order and records the prompts."""
    sent = []

    def gen(prompt, system=None, **kw):
        sent.append((system or "", prompt))
        return llm.LLMResult(json.dumps(replies[min(len(sent), len(replies)) - 1]), "gemini-test", 1, 1, 1)
    monkeypatch.setattr(llm, "generate", gen)
    return sent


def url(c, tail=""):
    return f"/api/quiz/attempts/{c['quiz']['id']}/debrief{tail}"


def test_teacher_asks_why_for_each_wrong_answer_and_records_findings(client, monkeypatch):
    c = ctx(client, n_correct=8)  # 2 wrong
    d = client.get(url(c), headers=c["sh"]).json()
    assert d["status"] == "active" and d["total"] == 2 and d["position"] == 0
    assert "8 of 10" in d["messages"][0]["text"]
    opener = d["messages"][1]["text"]
    assert "Question 9" in opener and "What made that option look right" in opener and "wrong" in opener  # their answer + the question why
    assert client.get(url(c), headers=c["sh"]).json()["id"] == d["id"]  # resumes, never duplicates
    sent = teacher(monkeypatch, [
        {"reply_md": "I see - you mixed it up with a similar idea. Here is the difference... Which one stores a value?",
         "diagnosis": "concept_gap", "finding": "Confuses the two definitions.", "move_on": False},
        {"reply_md": "Exactly right!", "diagnosis": "concept_gap", "finding": "Confused the two definitions; fixed now.", "move_on": True},
    ])
    d = client.post(url(c, "/reply"), headers=c["sh"], json={"message": "I thought wrong meant the same thing as right."}).json()
    assert d["position"] == 0 and d["messages"][-1]["text"].startswith("I see")
    system, prompt = sent[-1]
    assert "teacher" in system and "CORRECT ANSWER: " in prompt and "<student_input>" in prompt
    d = client.post(url(c, "/reply"), headers=c["sh"], json={"message": "The first one."}).json()
    assert d["position"] == 1 and "Question 10" in d["messages"][-1]["text"]  # moved on to the next wrong answer
    d = client.post(url(c, "/skip"), headers=c["sh"]).json()
    assert d["status"] == "done" and "revisit" in d["messages"][-1]["text"]
    assert [f["diagnosis"] for f in d["findings"]] == ["concept_gap", "unclear"]
    assert client.post(url(c, "/reply"), headers=c["sh"], json={"message": "more"}).status_code == 409


def test_debrief_findings_shape_the_reteach_explanation(client, monkeypatch):
    c = ctx(client, n_correct=2)  # below the target -> reteach
    client.get(url(c), headers=c["sh"])
    teacher(monkeypatch, [{"reply_md": "Got it.", "diagnosis": "misread", "finding": "Read 'not' as 'is'.", "move_on": True}])
    client.post(url(c, "/reply"), headers=c["sh"], json={"message": "I didn't see the word NOT"})
    sent = teacher(monkeypatch, ["unused"])
    monkeypatch.setattr(llm, "generate", lambda prompt, system=None, **kw: (sent.append((system, prompt)), llm.LLMResult("Another look...", "g", 1, 1, 1))[1])
    cyc = client.get(f"/api/reteach/{c['course']['id']}/clo/{c['clo']['id']}", headers=c["sh"])
    assert cyc.status_code == 200, cyc.text
    prompt = sent[-1][1]
    assert "WHAT THE LEARNER TOLD THEIR TEACHER" in prompt and "Read 'not' as 'is'" in prompt and "didn't see the word NOT" in prompt


def test_debrief_offline_still_walks_through_each_question(client):
    c = ctx(client, n_correct=9)
    client.get(url(c), headers=c["sh"])
    d = client.post(url(c, "/reply"), headers=c["sh"], json={"message": "I guessed"}).json()
    assert d["status"] == "done" and "key idea" in d["messages"][-2]["text"]


def test_all_correct_needs_no_debrief_and_unfinished_quiz_has_none(client):
    c = ctx(client, n_correct=10)
    d = client.get(url(c), headers=c["sh"]).json()
    assert d["status"] == "done" and d["total"] == 0 and "all 10" in d["messages"][0]["text"]
    from .helpers import walk_to_quiz

    clo2 = c["course"]["clos"][1]
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=1)  # top up so CLO2 has a quiz too
    walk_to_quiz(client, c["sh"], c["course"]["id"], clo2["id"])
    q = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": clo2["id"]})
    if q.status_code == 200:
        assert client.get(f"/api/quiz/attempts/{q.json()['id']}/debrief", headers=c["sh"]).status_code == 409


# ---- practice: hints and one set per lecture ------------------------------------------------------------------

def practice_fake(monkeypatch):
    calls = []

    def gen(prompt, system=None, **kw):
        calls.append(prompt)
        qs = [{"stem": f"Practice {len(calls)}-{i}?", "options": ["right", "wrong a", "wrong b", "wrong c"], "answer_index": 0,
               "explanation": "Because.", "hints": ["Think about what the word means.", "The answer is right"]} for i in range(5)]
        return llm.LLMResult(json.dumps({"questions": qs}), "gemini-test", 1, 1, 1)
    monkeypatch.setattr(llm, "generate", gen)
    return calls


def test_practice_has_hints_and_each_topic_keeps_its_own_set(client, monkeypatch):
    from app.db import SessionLocal
    from app.models.topics import Topic

    c = published_diagnostic(client)
    cid, clo = c["course"]["id"], c["course"]["clos"][0]["id"]
    db = SessionLocal()
    t1 = Topic(course_id=cid, number=1, title="Loops", summary="for and while", clo_ids=[clo], status="published", origin="manual")
    t2 = Topic(course_id=cid, number=2, title="Recursion", summary="base cases", clo_ids=[clo], status="published", origin="manual")
    db.add_all([t1, t2])
    db.commit()
    t1_id, t2_id = t1.id, t2.id
    db.close()
    calls = practice_fake(monkeypatch)
    s1 = client.post(f"/api/practice/{cid}/clos/{clo}", headers=c["sh"], json={"topic_id": t1_id}).json()
    assert s1["questions"][0]["hints"] == ["Think about what the word means."]  # the hint naming the answer is dropped
    assert "Loops" in calls[-1] and "this lecture topic only" in calls[-1]
    s2 = client.post(f"/api/practice/{cid}/clos/{clo}", headers=c["sh"], json={"topic_id": t2_id}).json()
    assert "Recursion" in calls[-1] and s2["id"] != s1["id"]
    latest = lambda t: client.get(f"/api/practice/{cid}/clos/{clo}/latest?topic_id={t}", headers=c["sh"]).json()
    assert latest(t1_id)["id"] == s1["id"] and latest(t2_id)["id"] == s2["id"]
    assert client.get(f"/api/practice/{cid}/clos/{clo}/latest", headers=c["sh"]).json() is None


# ---- Socratic nudging -----------------------------------------------------------------------------------------

def test_lesson_tutor_nudges_typed_questions(client, monkeypatch):
    from .test_tutor import setup, tutor_fake

    c, cid, t, _ = setup(client, monkeypatch)
    sent = tutor_fake(monkeypatch, {"reply_md": "Good question! Look at section 1 - what happens after each step?", "cited_sections": [1],
                                    "question": "What happens after each step?"})
    r = client.post(f"/api/tutor/{cid}/topics/{t['id']}", headers=c["sh"], json={"mode": "ask", "message": "What is a step?", "history": []})
    assert r.status_code == 200 and r.json()["question"]
    assert "SOCRATIC NUDGING" in sent[-1][1] and "do NOT answer it" in sent[-1][1]
    assert client.post(f"/api/tutor/{cid}/topics/{t['id']}", headers=c["sh"], json={"mode": "ask", "message": " ", "history": []}).status_code == 422


def test_unit_tutor_nudges_students_with_history(client, monkeypatch):
    c = published_diagnostic(client)
    take_diagnostic(client, c)
    sent = []

    def gen(prompt, system=None, **kw):
        sent.append(prompt)
        return llm.LLMResult("What do you already know about loops?", "gemini-test", 1, 1, 1)
    monkeypatch.setattr(llm, "generate", gen)
    r = client.post("/api/ai/ask", headers=c["sh"], json={"course_id": c["course"]["id"], "question": "How does recursion work?",
                                                          "history": [{"question": "What is a function?", "answer": "What does it take in?"}]})
    assert r.status_code == 200
    assert "SOCRATIC NUDGING" in sent[-1] and "What is a function?" in sent[-1]
    # staff testing the tutor get plain answers
    client.post("/api/ai/ask", headers=c["ih"], json={"course_id": c["course"]["id"], "question": "How does recursion work?"})
    assert "SOCRATIC NUDGING" not in sent[-1]
