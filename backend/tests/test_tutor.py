"""In-lesson Socratic tutor: explains highlighted text citing lesson sections, runs one-question checks with
hints before answers, works (explain only) when the AI is offline, and saves notes / flashcards."""
import json

from app.services import llm

from .helpers import login, make_user, published_diagnostic, take_diagnostic
from .test_topics import fake


def setup(client, monkeypatch):
    c = published_diagnostic(client)
    calls = fake(monkeypatch)
    take_diagnostic(client, c)
    cid = c["course"]["id"]
    t = client.get(f"/api/learn/{cid}/topics", headers=c["sh"]).json()[0]
    client.get(f"/api/learn/{cid}/topics/{t['id']}/lesson", headers=c["sh"])  # the student's lesson exists
    return c, cid, t, calls


def tutor_fake(monkeypatch, reply):
    sent = []

    def gen(prompt, system=None, **kw):
        sent.append((system or "", prompt))
        return llm.LLMResult(json.dumps(reply), "gemini-test", 1, 1, 1)
    monkeypatch.setattr(llm, "generate", gen)
    return sent


def test_explain_highlighted_text_cites_lesson_sections(client, monkeypatch):
    c, cid, t, _ = setup(client, monkeypatch)
    sent = tutor_fake(monkeypatch, {"reply_md": "A step means one move. What changes after each step?", "cited_sections": [1, 99],
                                    "question": "", "flashcard": {"front": "What is a step?", "back": "One move."}})
    r = client.post(f"/api/tutor/{cid}/topics/{t['id']}", headers=c["sh"], json={"mode": "explain", "selection": "Explained step by step", "history": []}).json()
    assert r["cited"] == [{"n": 1, "title": "This topic"}]  # the made-up section 99 is dropped
    assert r["flashcard"]["front"] == "What is a step?"
    system, prompt = sent[-1]
    assert "Socratic" in system and "never give a full answer immediately" in system
    assert "[SECTION 1: This topic]" in prompt and "Explained step by step" in prompt


def test_check_then_answer_gives_hints_first(client, monkeypatch):
    c, cid, t, _ = setup(client, monkeypatch)
    tutor_fake(monkeypatch, {"reply_md": "Why does order matter here?", "cited_sections": [1], "question": "Why does order matter here?"})
    q = client.post(f"/api/tutor/{cid}/topics/{t['id']}", headers=c["sh"], json={"mode": "check", "history": []}).json()
    assert q["question"] == "Why does order matter here?"
    sent = tutor_fake(monkeypatch, {"reply_md": "Close. Hint: look at section 1. What happens first?", "cited_sections": [1]})
    a = client.post(f"/api/tutor/{cid}/topics/{t['id']}", headers=c["sh"], json={
        "mode": "answer", "message": "no idea", "history": [{"role": "tutor", "text": q["reply_md"]}]}).json()
    assert a["cited"][0]["n"] == 1
    assert "do NOT give the full answer" in sent[-1][1] and "CONVERSATION SO FAR" in sent[-1][1]


def test_offline_explain_shows_the_lesson_section_and_check_is_unavailable(client, monkeypatch):
    c = published_diagnostic(client)
    take_diagnostic(client, c)
    cid = c["course"]["id"]
    t = client.get(f"/api/learn/{cid}/topics", headers=c["sh"]).json()[0]
    r = client.post(f"/api/tutor/{cid}/topics/{t['id']}", headers=c["sh"], json={"mode": "explain", "selection": "key words", "history": []}).json()
    assert r["offline"] is True and r["cited"]
    assert client.post(f"/api/tutor/{cid}/topics/{t['id']}", headers=c["sh"], json={"mode": "check", "history": []}).status_code == 503


def test_notes_and_flashcards_are_private(client, monkeypatch):
    c, cid, t, _ = setup(client, monkeypatch)
    n = client.post(f"/api/tutor/{cid}/notes", headers=c["sh"], json={"topic_id": t["id"], "kind": "flashcard", "front": "Q?", "back": "A."})
    assert n.status_code == 201
    assert [x["front"] for x in client.get(f"/api/tutor/{cid}/notes?topic_id={t['id']}", headers=c["sh"]).json()] == ["Q?"]
    other = login(client, make_user("student").email)
    assert client.delete(f"/api/tutor/{cid}/notes/{n.json()['id']}", headers=other).status_code in (403, 404)
    assert client.delete(f"/api/tutor/{cid}/notes/{n.json()['id']}", headers=c["sh"]).status_code == 200


def test_hint_and_reveal_only_after_an_attempt(client, monkeypatch):
    c, cid, t, _ = setup(client, monkeypatch)
    tutor_fake(monkeypatch, {"reply_md": "Hint: see section 1.", "cited_sections": [1]})
    url = f"/api/tutor/{cid}/topics/{t['id']}"
    hist = [{"role": "tutor", "text": "Why does order matter?"}]
    assert client.post(url, headers=c["sh"], json={"mode": "hint", "history": hist}).status_code == 200
    assert client.post(url, headers=c["sh"], json={"mode": "reveal", "history": hist}).status_code == 409
    hist.append({"role": "student", "text": "Answer: because steps depend on each other"})
    assert client.post(url, headers=c["sh"], json={"mode": "reveal", "history": hist}).status_code == 200
