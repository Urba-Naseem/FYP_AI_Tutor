"""US-4.1 Deliver the diagnostic."""
from .helpers import right, wrong
from .helpers import active_course_with_student, make_items, published_diagnostic, take_diagnostic


def test_not_available_until_published(client):
    c = active_course_with_student(client)
    assert client.post(f"/api/diagnostic/{c['course']['id']}/start", headers=c["sh"]).status_code == 409


def test_questions_never_expose_answers(client):
    c = published_diagnostic(client)
    st = client.post(f"/api/diagnostic/{c['course']['id']}/start", headers=c["sh"]).json()
    q = st["question"]
    assert set(q) == {"id", "stem", "options", "clo_id"} and st["total"] == 15


def test_adaptive_selection_covers_every_clo_and_adjusts_difficulty(client):
    c = published_diagnostic(client)
    st = client.post(f"/api/diagnostic/{c['course']['id']}/start", headers=c["sh"]).json()
    seen = []
    items = {i["id"]: i for i in c["items"]}
    while st["status"] == "in_progress":
        q = st["question"]
        seen.append(items[q["id"]])
        st = client.post(f"/api/diagnostic/attempts/{st['attempt_id']}/answer", headers=c["sh"], json={"item_id": q["id"], "choice": right(q)}).json()
    # first 5 questions: one per CLO (balanced coverage), each starting at medium difficulty
    assert len({i["clo_id"] for i in seen[:5]}) == 5 and all(i["difficulty"] == 2 for i in seen[:5])
    # after a correct answer the next item for that CLO is harder
    assert all(i["difficulty"] == 3 for i in seen[5:10])
    assert len(seen) == 15


def test_autosave_and_resume(client):
    c = published_diagnostic(client)
    cid = c["course"]["id"]
    st = client.post(f"/api/diagnostic/{cid}/start", headers=c["sh"]).json()
    st = client.post(f"/api/diagnostic/attempts/{st['attempt_id']}/answer", headers=c["sh"], json={"item_id": st["question"]["id"], "choice": right(st["question"])}).json()
    q2 = st["question"]
    assert client.put(f"/api/diagnostic/attempts/{st['attempt_id']}/draft", headers=c["sh"], json={"item_id": q2["id"], "choice": 2}).status_code == 200
    # browser closed -> start again resumes at the same question with the draft choice
    resumed = client.post(f"/api/diagnostic/{cid}/start", headers=c["sh"]).json()
    assert resumed["attempt_id"] == st["attempt_id"] and resumed["question"]["id"] == q2["id"]
    assert resumed["draft_choice"] == 2 and resumed["answered"] == 1


def test_per_clo_readiness_results(client):
    c = published_diagnostic(client)
    st = take_diagnostic(client, c, correct_clos={"CLO1", "CLO2"})
    res = {r["clo_code"]: r for r in st["results"]}
    assert res["CLO1"]["level"] == "ready" and res["CLO1"]["score_pct"] == 100
    assert res["CLO3"]["level"] == "not_ready" and res["CLO3"]["answered"] == 3
    status = client.get(f"/api/diagnostic/{c['course']['id']}/status", headers=c["sh"]).json()
    assert status["attempt"]["status"] == "completed" and len(status["results"]) == 5
    assert client.post(f"/api/diagnostic/{c['course']['id']}/start", headers=c["sh"]).status_code == 409


def test_instructor_sees_results_and_can_reset(client):
    c = published_diagnostic(client)
    st = take_diagnostic(client, c)
    rows = client.get(f"/api/diagnostic/{c['course']['id']}/results", headers=c["ih"]).json()
    assert rows[0]["status"] == "completed"
    assert client.post(f"/api/diagnostic/attempts/{st['attempt_id']}/reset", headers=c["ih"], json={"reason": "Technical issue"}).status_code == 200
    assert client.post(f"/api/diagnostic/{c['course']['id']}/start", headers=c["sh"]).status_code == 200


def test_answers_recorded_as_learning_events(client):
    c = published_diagnostic(client)
    take_diagnostic(client, c)
    ev = client.get("/api/audit?category=learning&action=learn.diagnostic", headers=c["ah"]).json()
    assert ev["total"] == 17  # start + 15 answers + completion


def test_bank_exhaustion_does_not_block(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], per_clo=3)
    client.put(f"/api/diagnostic/config/{c['course']['id']}", headers=c["ih"], json={"min_items_per_clo": 3, "items_per_clo": 2})
    client.post(f"/api/diagnostic/publish/{c['course']['id']}", headers=c["ih"])
    st = take_diagnostic(client, c)
    assert st["status"] == "completed" and sum(r["answered"] for r in st["results"]) == 10
