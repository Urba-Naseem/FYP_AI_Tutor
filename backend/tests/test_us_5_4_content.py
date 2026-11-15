"""US-5.4 Draft and approve learning content."""
from app.services import llm

from .helpers import active_course_with_student, add_approved_source


def ctx(client, source=True):
    c = active_course_with_student(client)
    if source:
        add_approved_source(client, c["ih"], c["ih"], c["course"]["id"])
    return c


def test_offline_draft_is_source_grounded_and_pending(client):
    c = ctx(client)
    unit = c["course"]["units"][2]
    r = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["course"]["id"], "unit_id": unit["id"]})
    assert r.status_code == 201
    d = r.json()
    assert d["version"]["status"] == "pending" and d["version"]["origin"] == "offline" and d["version"]["source_chunk_ids"]
    assert "Source: [S1]" in d["version"]["body_md"] and d["approved_version"] is None
    assert client.get("/api/reviews?kind=content", headers=c["ih"]).json()


def test_draft_without_sources_uses_learning_outcomes(client, monkeypatch):
    """Teaching sources are optional: offline gives an outcome-based outline draft, live writes from the CLOs."""
    c = ctx(client, source=False)
    unit = c["course"]["units"][0]
    r = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["course"]["id"], "unit_id": unit["id"]})
    assert r.status_code == 201
    v = r.json()["version"]
    assert v["origin"] == "offline" and v["source_chunk_ids"] == [] and "learning outcomes" in v["body_md"]
    seen = {}

    def gen(prompt, system=None, **kw):
        seen["prompt"] = prompt
        return llm.LLMResult("# Unit\n\nA lesson written from the outcomes.", "gemini-test", 9, 9, 9)
    monkeypatch.setattr(llm, "generate", gen)
    d = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["course"]["id"], "unit_id": unit["id"]}).json()
    assert d["version"]["origin"] == "ai" and "COURSE OUTLINE:" in seen["prompt"] and "SOURCES:" not in seen["prompt"]


def test_live_draft_traced(client, monkeypatch):
    c = ctx(client)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: llm.LLMResult("# Functions\n\nA function packages a computation [S1].", "gemini-test", 9, 9, 9))
    d = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["course"]["id"], "unit_id": c["course"]["units"][2]["id"]}).json()
    assert d["version"]["origin"] == "ai" and d["version"]["ai_call_id"]


def test_approval_history_and_supersede(client):
    c = ctx(client)
    unit = c["course"]["units"][0]
    d = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["course"]["id"], "unit_id": unit["id"]}).json()
    doc = client.post(f"/api/content/versions/{d['version']['id']}/review", headers=c["ih"], json={"decision": "approve", "note": "Good"}).json()
    assert doc["approved_version"] == 1
    v2 = client.post(f"/api/content/{d['id']}/versions", headers=c["ih"], json={"body_md": d["version"]["body_md"] + "\n\nExtra worked example.", "change_summary": "Added example"}).json()
    # until v2 is approved, v1 stays the published one
    assert client.get(f"/api/content/{d['id']}", headers=c["ih"]).json()["approved_version"] == 1
    doc = client.post(f"/api/content/versions/{v2['id']}/review", headers=c["ih"], json={"decision": "approve"}).json()
    assert doc["approved_version"] == 2
    assert [v["status"] for v in doc["versions"]] == ["approved", "superseded"]
    diff = client.get(f"/api/content/versions/{d['version']['id']}/diff/{v2['id']}", headers=c["ih"]).json()["diff"]
    assert "+Extra worked example." in diff


def test_alternate_explanation_per_clo(client):
    c = ctx(client)
    clo = c["course"]["clos"][4]
    unit = next(u for u in c["course"]["units"] if u["id"] == clo["unit_id"])
    r = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["course"]["id"], "unit_id": unit["id"], "kind": "alternate", "clo_id": clo["id"]})
    assert r.status_code == 201 and r.json()["kind"] == "alternate" and r.json()["clo_code"] == "CLO5"


def test_reject_needs_reason_and_students_blocked(client):
    c = ctx(client)
    d = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["course"]["id"], "unit_id": c["course"]["units"][0]["id"]}).json()
    assert client.post(f"/api/content/versions/{d['version']['id']}/review", headers=c["ih"], json={"decision": "reject"}).status_code == 422
    assert client.get(f"/api/content?course_id={c['course']['id']}", headers=c["sh"]).status_code == 403
