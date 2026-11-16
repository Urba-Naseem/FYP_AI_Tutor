"""US-6.2 Provide guided hints."""
from app.services import llm

from .helpers import walk_to_quiz
from .helpers import right, wrong
from .helpers import add_approved_source, active_course_with_student, make_items


def ctx(client, source=True):
    c = active_course_with_student(client)
    if source:
        add_approved_source(client, c["ih"], c["ih"], c["course"]["id"])
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=5)
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["course"]["clos"][0]["id"])  # quizzes open in path order: start the path first
    q = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["course"]["clos"][0]["id"]}).json()
    c["quiz"], c["q"] = q, q["questions"][0]
    return c


def hint(client, c):
    return client.post(f"/api/quiz/attempts/{c['quiz']['id']}/hint", headers=c["sh"], json={"item_id": c["q"]["id"]})


def test_static_fallback_is_progressive(client):
    c = ctx(client)
    h1, h2, h3 = hint(client, c).json(), hint(client, c).json(), hint(client, c).json()
    assert [h1["level"], h2["level"], h3["level"]] == [1, 2, 3]
    assert h1["mode"] == h2["mode"] == "static"
    assert h1["text"] == "Think about the definition." and h2["text"] == "Look at the example."
    assert "simpler version" in h3["text"]  # generic level-3 prompt when no static hint
    assert hint(client, c).status_code == 409


def test_socratic_hint_from_gemini(client, monkeypatch):
    c = ctx(client)
    captured = {}

    def gen(prompt, system=None, **kw):
        captured["prompt"] = prompt
        return llm.LLMResult("Which part of the notes explains how assignment works?", "gemini-test", 1, 1, 1)

    monkeypatch.setattr(llm, "generate", gen)
    h = hint(client, c).json()
    assert h["mode"] == "socratic" and h["text"].endswith("?")
    assert "answer_index" not in captured["prompt"] and "Level 1" in captured["prompt"]


def test_hint_that_leaks_answer_is_blocked(client, monkeypatch):
    c = ctx(client)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: llm.LLMResult("The correct answer is right 0.", "gemini-test", 1, 1, 1))
    h = hint(client, c).json()
    assert h["mode"] == "protected" and "right 0" not in h["text"]
    calls = client.get("/api/ai/calls?status=blocked", headers=c["ah"]).json()
    assert calls["total"] == 1


def test_hints_recorded_with_answer_and_events(client):
    c = ctx(client)
    hint(client, c)
    hint(client, c)
    client.post(f"/api/quiz/attempts/{c['quiz']['id']}/answer", headers=c["sh"], json={"item_id": c["q"]["id"], "choice": right(c["q"])})
    assert hint(client, c).status_code == 409  # no hints after answering
    view = client.get(f"/api/quiz/attempts/{c['quiz']['id']}", headers=c["sh"]).json()
    assert view["questions"][0]["answer"]["hints_used"] == 2
    assert client.get("/api/audit?action=learn.hint_requested", headers=c["ah"]).json()["total"] == 2


def test_hints_listed_for_resume(client):
    c = ctx(client)
    hint(client, c)
    rows = client.get(f"/api/quiz/attempts/{c['quiz']['id']}/hints/{c['q']['id']}", headers=c["sh"]).json()
    assert rows[0]["level"] == 1
