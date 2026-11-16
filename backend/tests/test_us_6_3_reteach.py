"""US-6.3 Reteach below mastery."""
from app.services import llm

from .helpers import right, wrong
from .helpers import add_approved_source, make_items, published_diagnostic, take_diagnostic, take_quiz


def ctx(client, items=15, source=True):
    c = published_diagnostic(client)
    if source:
        add_approved_source(client, c["ih"], c["ih"], c["course"]["id"])
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=items)
    take_diagnostic(client, c)
    c["cid"] = c["course"]["id"]
    c["clo"] = next(o for o in c["course"]["clos"] if o["code"] == "CLO2")
    # fail the practice quiz badly -> A-REMEDIAL inserts a reteach step
    step = next(s for s in client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()["steps"]
                if s["clo_code"] == "CLO2" and s["step_type"] == "practice")
    take_quiz(client, c["sh"], c["cid"], c["clo"]["id"], 0, step["id"])
    return c


def reteach_step(client, c):
    p = client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()
    return next(s for s in p["steps"] if s["clo_code"] == "CLO2" and s["step_type"] == "reteach")


def reteach_quiz(client, c, n_correct):
    cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
    q = client.post(f"/api/reteach/cycles/{cyc['id']}/quiz", headers=c["sh"], json={"step_id": reteach_step(client, c)["id"]})
    assert q.status_code == 200, q.text
    q = q.json()
    for i, qq in enumerate(q["questions"]):
        client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": qq["id"], "choice": right(qq) if i < n_correct else wrong(qq)})
    return q, client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"]).json()


def test_alternate_explanation_offline_from_sources(client):
    c = ctx(client)
    cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
    assert cyc["explanation_source"] == "offline_extract" and "approved course notes" in cyc["explanation_md"]
    assert cyc["attempts_left"] == 3 and cyc["fresh_questions"] >= 3


def test_approved_alternate_preferred(client):
    c = ctx(client)
    unit = next(u for u in c["course"]["units"] if u["id"] == c["clo"]["unit_id"])
    d = client.post("/api/content/draft", headers=c["ih"], json={"course_id": c["cid"], "unit_id": unit["id"], "kind": "alternate", "clo_id": c["clo"]["id"]}).json()
    client.post(f"/api/content/versions/{d['version']['id']}/review", headers=c["ih"], json={"decision": "approve"})
    cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
    assert cyc["explanation_source"] == "approved_alternate"


def test_ai_explanation_is_grounded_and_routed(client, monkeypatch):
    c = ctx(client)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: llm.LLMResult("Think of precedence like BODMAS [S1].", "gemini-test", 1, 1, 1))
    cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
    assert cyc["explanation_source"] == "ai_grounded"
    assert any("reteach explanation" in t["title"] for t in client.get("/api/reviews", headers=c["ih"]).json())


def test_reteach_uses_new_questions_each_time(client):
    c = ctx(client)
    seen = set()
    q1, r1 = reteach_quiz(client, c, 1)
    ids1 = {q["id"] for q in q1["questions"]}
    q2, r2 = reteach_quiz(client, c, 1)
    ids2 = {q["id"] for q in q2["questions"]}
    assert not ids1 & ids2 and r1["adaptation"]["rule"] == "R-RETRY"
    seen |= ids1


def test_pass_advances(client):
    c = ctx(client)
    _, r = reteach_quiz(client, c, 5)
    assert r["adaptation"]["rule"] == "R-PASS" and r["mastery"]["attained"]
    assert reteach_step_status(client, c) == "done"


def reteach_step_status(client, c):
    p = client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()
    return next(s for s in p["steps"] if s["clo_code"] == "CLO2" and s["step_type"] == "reteach")["status"]


def test_attempt_limit_then_advance_with_flag(client):
    c = ctx(client, items=20)
    rules = [reteach_quiz(client, c, 0)[1]["adaptation"]["rule"] for _ in range(3)]
    assert rules == ["R-RETRY", "R-RETRY", "R-EXHAUSTED"]
    p = client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()
    step = next(s for s in p["steps"] if s["clo_code"] == "CLO2" and s["step_type"] == "reteach")
    assert step["status"] == "skipped" and step["meta"]["flag"] == "needs_instructor"
    assert p["next_step_id"] is not None  # learner is not trapped
    cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
    assert cyc["status"] == "exhausted"


def test_not_enough_new_questions(client):
    c = ctx(client, items=6)  # 5 used by the practice quiz -> only 1 unseen left
    cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
    assert client.post(f"/api/reteach/cycles/{cyc['id']}/quiz", headers=c["sh"], json={}).status_code == 409
