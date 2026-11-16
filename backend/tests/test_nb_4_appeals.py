"""NB-4 Review and resolve assessment appeals."""
from app.services.email import list_outbox

from .helpers import active_course_with_student, login, make_items, make_user, take_quiz


def ctx(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=6)
    c["cid"] = c["course"]["id"]
    c["clo"] = c["course"]["clos"][0]
    c["attempt"] = take_quiz(client, c["sh"], c["cid"], c["clo"]["id"], 2)  # 2/5 = 40%, not attained
    return c


def appeal(client, c, reason="Question 3 has two correct options; mine was also right."):
    return client.post("/api/appeals", headers=c["sh"], json={"quiz_attempt_id": c["attempt"]["id"], "item_id": c["attempt"]["questions"][2]["id"], "reason": reason})


def test_submit_routes_to_current_instructor(client):
    c = ctx(client)
    assert appeal(client, c, "too short").status_code == 422
    r = appeal(client, c)
    assert r.status_code == 201 and r.json()["status"] == "submitted" and r.json()["original_score"] == "2/5"
    assert appeal(client, c).status_code == 409  # one open appeal per attempt
    assert any(t["kind"] == "appeal" for t in client.get("/api/reviews", headers=c["ih"]).json())
    elig = client.get(f"/api/appeals/eligible/{c['cid']}", headers=c["sh"]).json()
    assert elig[0]["has_open_appeal"] is True


def test_upheld_appeal_amends_grade_in_controlled_way(client):
    c = ctx(client)
    aid = appeal(client, c).json()["id"]
    client.post(f"/api/appeals/{aid}/start-review", headers=c["ih"])
    assert client.post(f"/api/appeals/{aid}/resolve", headers=c["ih"], json={"decision": "upheld", "resolution": "Option B also correct"}).status_code == 422
    r = client.post(f"/api/appeals/{aid}/resolve", headers=c["ih"], json={"decision": "upheld", "resolution": "Option B is also correct; key fixed", "new_correct": 3})
    assert r.status_code == 200 and r.json()["amended_score"] == "3/5"
    m = client.get(f"/api/mastery/course/{c['cid']}/student/{c['student'].id}", headers=c["ih"]).json()["clos"][0]
    statuses = sorted((e["version_no"], e["status"]) for e in m["evidence"])
    assert statuses == [(1, "superseded"), (2, "active")]  # original kept, new version active
    assert m["current"]["attained"] is True and m["current"]["trigger"] == "amendment"
    acts = {e["action"] for e in client.get("/api/audit?action=", headers=c["ah"]).json()["items"]}
    assert {"appeal.upheld", "mastery.evidence_amended"} <= acts


def test_rejected_appeal_notifies_student(client):
    c = ctx(client)
    aid = appeal(client, c).json()["id"]
    client.post(f"/api/appeals/{aid}/resolve", headers=c["ih"], json={"decision": "rejected", "resolution": "Only option A is correct per the notes"})
    notes = client.get("/api/notifications", headers=c["sh"]).json()
    assert notes[0]["title"] == "Appeal rejected" and "2/5 stands" in notes[0]["body"]
    assert "Appeal rejected" in list_outbox()[0]["subject"]
    client.post(f"/api/notifications/{notes[0]['id']}/read", headers=c["sh"])
    assert client.get("/api/notifications", headers=c["sh"]).json()[0]["read"] is True
    assert client.get("/api/appeals/mine", headers=c["sh"]).json()[0]["status"] == "rejected"


def test_only_course_instructor_resolves(client):
    c = ctx(client)
    aid = appeal(client, c).json()["id"]
    other = make_user("instructor")
    r = client.post(f"/api/appeals/{aid}/resolve", headers=login(client, other.email), json={"decision": "rejected", "resolution": "Not my course at all"})
    assert r.status_code == 403
    assert client.post(f"/api/appeals/{aid}/resolve", headers=c["sh"], json={"decision": "upheld", "resolution": "I upheld it myself", "new_correct": 5}).status_code == 403


def test_cannot_appeal_someone_elses_attempt(client):
    c = ctx(client)
    other = make_user("student")
    r = client.post("/api/appeals", headers=login(client, other.email), json={"quiz_attempt_id": c["attempt"]["id"], "reason": "This is not my attempt but I appeal"})
    assert r.status_code == 404
