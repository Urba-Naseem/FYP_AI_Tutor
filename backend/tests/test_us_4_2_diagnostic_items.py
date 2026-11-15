"""US-4.2 Generate and approve diagnostic items."""
from app.services import llm

from .helpers import add_approved_source, active_course_with_student, make_items


def ctx(client, source=True):
    c = active_course_with_student(client)
    if source:
        add_approved_source(client, c["ih"], c["ih"], c["course"]["id"])
    return c


def test_offline_generation_is_grounded_and_pending(client):
    c = ctx(client)
    r = client.post("/api/items/generate", headers=c["ih"], json={"course_id": c["course"]["id"], "purpose": "diagnostic", "count": 3}).json()
    assert r["origins"] == ["offline"] and len(r["created"]) >= 5
    it = r["created"][0]
    assert it["status"] == "pending" and len(it["options"]) == 4 and it["source_chunk_ids"]
    assert "course notes" in it["stem"]
    tasks = client.get("/api/reviews?kind=diagnostic_item", headers=c["ih"]).json()
    assert len(tasks) == len(r["created"])


def test_offline_generation_without_sources_explains_why(client):
    c = ctx(client, source=False)
    r = client.post("/api/items/generate", headers=c["ih"], json={"course_id": c["course"]["id"], "count": 2}).json()
    assert r["created"] == [] and "AI service is unavailable" in r["problems"][0]


def test_live_generation_from_clos_without_sources(client, monkeypatch):
    """Teaching sources are optional: items are written from the CLO and course outline."""
    c = ctx(client, source=False)
    seen = {}
    payload = ('{"items":[{"stem":"Which value does 2 + 3 * 4 produce?","options":["14","20","24","9"],"answer_index":0,'
               '"explanation":"* binds before +","hints":["Which operator binds first?"],"difficulty":1}]}')

    def gen(prompt, system=None, **kw):
        seen["prompt"] = prompt
        return llm.LLMResult(payload, "gemini-test", 5, 5, 5)
    monkeypatch.setattr(llm, "generate", gen)
    clo = c["course"]["clos"][1]
    r = client.post("/api/items/generate", headers=c["ih"], json={"course_id": c["course"]["id"], "clo_id": clo["id"], "count": 1}).json()
    assert r["origins"] == ["ai"] and len(r["created"]) == 1 and r["created"][0]["source_chunk_ids"] == []
    assert "COURSE OUTLINE:" in seen["prompt"] and clo["code"] in seen["prompt"] and "SOURCES:" not in seen["prompt"]


def test_live_generation_validates_and_protects_hints(client, monkeypatch):
    c = ctx(client)
    payload = ('{"items":[{"stem":"What does 2 + 3 * 4 evaluate to?","options":["14","20","24","9"],"answer_index":0,'
               '"explanation":"* first [S1]","hints":["Which operator binds first?","It is 14"],"difficulty":1},'
               '{"stem":"bad","options":["a","a"],"answer_index":5}]}')
    monkeypatch.setattr(llm, "generate", lambda *a, **k: llm.LLMResult(payload, "gemini-test", 5, 5, 5))
    clo = c["course"]["clos"][1]
    r = client.post("/api/items/generate", headers=c["ih"], json={"course_id": c["course"]["id"], "clo_id": clo["id"], "count": 2}).json()
    assert r["origins"] == ["ai"] and len(r["created"]) == 1  # invalid item dropped
    assert r["created"][0]["hints"] == ["Which operator binds first?"]  # hint revealing the answer removed
    assert r["created"][0]["ai_call_id"]


def test_publish_blocked_until_every_clo_has_minimum(client):
    c = ctx(client)
    cid = c["course"]["id"]
    items = make_items(client, c["ih"], c["course"], per_clo=2)
    r = client.post(f"/api/diagnostic/publish/{cid}", headers=c["ih"])
    assert r.status_code == 409 and r.json()["detail"]["short"] == ["CLO1", "CLO2", "CLO3", "CLO4", "CLO5"]
    make_items(client, c["ih"], c["course"], per_clo=1)
    r = client.post(f"/api/diagnostic/publish/{cid}", headers=c["ih"])
    assert r.status_code == 200 and r.json()["published"] is True and r.json()["complete"] is True
    assert items


def test_pending_items_do_not_count(client):
    c = ctx(client)
    make_items(client, c["ih"], c["course"], per_clo=3, approve=False)
    cov = client.get(f"/api/items/coverage/{c['course']['id']}", headers=c["ih"]).json()
    assert cov["complete"] is False and cov["clos"][0]["pending"] == 3


def test_editing_approved_item_creates_new_version(client):
    c = ctx(client)
    it = make_items(client, c["ih"], c["course"], per_clo=1)[0]
    v2 = client.patch(f"/api/items/{it['id']}", headers=c["ih"], json={"stem": "Improved wording of the question?"}).json()
    assert v2["version_no"] == 2 and v2["status"] == "pending" and v2["supersedes_id"] == it["id"]
    client.post(f"/api/items/{v2['id']}/review", headers=c["ih"], json={"decision": "approve"})
    items = {i["id"]: i for i in client.get(f"/api/items?course_id={c['course']['id']}", headers=c["ih"]).json()}
    assert items[it["id"]]["status"] == "retired" and items[v2["id"]]["status"] == "approved"


def test_reject_needs_reason_and_students_cannot_see_bank(client):
    c = ctx(client)
    it = make_items(client, c["ih"], c["course"], per_clo=1, approve=False)[0]
    assert client.post(f"/api/items/{it['id']}/review", headers=c["ih"], json={"decision": "reject"}).status_code == 422
    assert client.get(f"/api/items?course_id={c['course']['id']}", headers=c["sh"]).status_code == 403


def test_answer_key_encrypted_at_rest(client, db):
    c = ctx(client)
    make_items(client, c["ih"], c["course"], per_clo=1)
    raw = db.execute(__import__("sqlalchemy").text("select answer_index from items limit 1")).scalar()
    assert raw.startswith("enc:")

def test_delete_draft_questions_then_approve_the_whole_questionnaire_once(client):
    """One teacher approval for the whole questionnaire (all types); drafts can be deleted first."""
    c = ctx(client, source=False)
    diag = make_items(client, c["ih"], c["course"], "diagnostic", per_clo=4, approve=False)
    prac = make_items(client, c["ih"], c["course"], "formative", per_clo=1, approve=False)
    # delete one unwanted draft; approved questions cannot be deleted (they are retired instead)
    gone = diag.pop()
    assert client.delete(f"/api/items/{gone['id']}", headers=c["ih"]).status_code == 200
    assert all(i["id"] != gone["id"] for i in client.get(f"/api/items?course_id={c['course']['id']}", headers=c["ih"]).json())
    r = client.post(f"/api/items/approve-all/{c['course']['id']}", headers=c["ih"], json={"note": "Checked"}).json()
    assert r["approved"] == len(diag) + len(prac) and r["by_type"] == {"diagnostic": len(diag), "formative": len(prac)}
    assert r["diagnostic_published"] is True and r["coverage"]["published"]
    assert client.get(f"/api/items?course_id={c['course']['id']}&status=pending", headers=c["ih"]).json() == []
    assert client.delete(f"/api/items/{diag[0]['id']}", headers=c["ih"]).status_code == 409
    assert client.post(f"/api/items/approve-all/{c['course']['id']}", headers=c["ih"], json={}).status_code == 409
    assert client.get("/api/reviews?kind=diagnostic_item&status=pending", headers=c["ih"]).json() == []
    ev = client.get("/api/audit?action=items.questionnaire_approved", headers=c["ah"]).json()
    assert ev["total"] == 1

def test_model_returning_a_bare_list_still_works(client, monkeypatch):
    """Regression: Gemini sometimes returns [ {...} ] instead of {"items": [ {...} ]} (was a 500 error)."""
    c = ctx(client, source=False)
    payload = ('[{"stem":"What does 2 + 3 * 4 evaluate to?","options":["14","20","24","9"],"answer_index":0,'
               '"explanation":"* binds first","hints":["Which operator binds first?"],"difficulty":1}]')
    monkeypatch.setattr(llm, "generate", lambda *a, **k: llm.LLMResult(payload, "gemini-test", 5, 5, 5))
    clo = c["course"]["clos"][1]
    r = client.post("/api/items/generate", headers=c["ih"], json={"course_id": c["course"]["id"], "clo_id": clo["id"], "count": 1})
    assert r.status_code == 200 and r.json()["origins"] == ["ai"] and len(r.json()["created"]) == 1


def test_json_shape_helpers():
    from app.services import ai_gateway
    assert ai_gateway.as_list([1, 2], "items") == [1, 2]
    assert ai_gateway.as_list({"items": [1]}, "items") == [1]
    assert ai_gateway.as_list({"questions": [1]}, "items") == [1]
    assert ai_gateway.as_list({"stem": "x"}, "items") == [{"stem": "x"}]
    assert ai_gateway.as_object([{"a": 1}]) == {"a": 1}
    import pytest
    with pytest.raises(ai_gateway.AIUnavailable):
        ai_gateway.as_object([1, 2])
