"""US-6.4 Handle escalated learners."""
from .helpers import right, wrong
from .helpers import make_items, published_diagnostic, take_diagnostic, take_quiz


def ctx(client):
    c = published_diagnostic(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=30)
    take_diagnostic(client, c)
    c["cid"] = c["course"]["id"]
    c["clo"] = next(o for o in c["course"]["clos"] if o["code"] == "CLO2")
    return c


def fail_until_exhausted(client, c):
    take_quiz(client, c["sh"], c["cid"], c["clo"]["id"], 0)  # -> reteach step
    for _ in range(3):
        cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
        q = client.post(f"/api/reteach/cycles/{cyc['id']}/quiz", headers=c["sh"], json={}).json()
        for qq in q["questions"]:
            client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": qq["id"], "choice": wrong(qq)})
        client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"])


def test_repeated_failure_raises_escalation(client):
    c = ctx(client)
    for _ in range(3):
        take_quiz(client, c["sh"], c["cid"], c["clo"]["id"], 2)  # 40% three times
    esc = client.get(f"/api/escalations?course_id={c['cid']}", headers=c["ih"]).json()
    assert len(esc) == 1 and esc[0]["trigger"] == "repeated_failure" and esc[0]["clo_code"] == "CLO2"
    assert any(t["kind"] == "escalation" for t in client.get("/api/reviews", headers=c["ih"]).json())


def test_exhausted_reteach_escalates_and_flags_console(client):
    c = ctx(client)
    fail_until_exhausted(client, c)
    esc = client.get(f"/api/escalations?course_id={c['cid']}", headers=c["ih"]).json()
    assert esc[0]["trigger"] == "reteach_exhausted"
    d = client.get(f"/api/console/course/{c['cid']}", headers=c["ih"]).json()
    assert any(f["type"] == "escalation" for f in d["needs_intervention"][0]["flags"])


def test_intervention_requires_reason(client):
    c = ctx(client)
    r = client.post("/api/interventions", headers=c["ih"], json={"student_id": c["student"].id, "course_id": c["cid"], "action": "note", "reason": "short"})
    assert r.status_code == 422


def test_reset_attempts_reopens_reteach(client):
    c = ctx(client)
    fail_until_exhausted(client, c)
    esc = client.get(f"/api/escalations?course_id={c['cid']}", headers=c["ih"]).json()[0]
    r = client.post("/api/interventions", headers=c["ih"], json={
        "student_id": c["student"].id, "course_id": c["cid"], "clo_id": c["clo"]["id"], "escalation_id": esc["id"],
        "action": "reset_attempts", "reason": "Met in office hours; worked through precedence together"})
    assert r.status_code == 201 and r.json()["details"]["previous_status"] == "exhausted"
    cyc = client.get(f"/api/reteach/{c['cid']}/clo/{c['clo']['id']}", headers=c["sh"]).json()
    assert cyc["status"] == "active" and cyc["attempts_left"] == 3
    assert client.get(f"/api/escalations?course_id={c['cid']}", headers=c["ih"]).json()[0]["status"] == "acknowledged"


def test_override_advance_and_mastery_override_recorded(client):
    c = ctx(client)
    take_quiz(client, c["sh"], c["cid"], c["clo"]["id"], 1)
    r = client.post("/api/interventions", headers=c["ih"], json={"student_id": c["student"].id, "course_id": c["cid"], "clo_id": c["clo"]["id"],
                                                                 "action": "override_advance", "reason": "Demonstrated skill in a lab practical"})
    assert r.json()["details"]["steps_skipped"] >= 1
    p = client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()
    assert all(s["status"] != "pending" for s in p["steps"] if s["clo_code"] == "CLO2") and p["trigger"] == "override"
    r = client.post("/api/interventions", headers=c["ih"], json={"student_id": c["student"].id, "course_id": c["cid"], "clo_id": c["clo"]["id"],
                                                                 "action": "mastery_override", "attained": True, "reason": "Assessed orally by instructor - attained"})
    assert r.json()["details"]["after"] == {"attained": True}
    m = client.get(f"/api/mastery/course/{c['cid']}/student/{c['student'].id}", headers=c["ih"]).json()
    cur = next(x for x in m["clos"] if x["clo_code"] == "CLO2")["current"]
    assert cur["attained"] is True and cur["trigger"] == "override" and "orally" in cur["note"]
    ev = client.get("/api/audit?action=intervention.", headers=c["ah"]).json()
    assert ev["total"] == 2 and ev["items"][0]["details"]["reason"]


def test_manual_escalation_and_resolution(client):
    c = ctx(client)
    e = client.post("/api/escalations", headers=c["ih"], json={"student_id": c["student"].id, "course_id": c["cid"], "summary": "Missed three weeks of class"}).json()
    client.post("/api/interventions", headers=c["ih"], json={"student_id": c["student"].id, "course_id": c["cid"], "escalation_id": e["id"],
                                                             "action": "meeting", "reason": "Met with student and agreed a catch-up plan", "resolve": True})
    e = client.get(f"/api/escalations?course_id={c['cid']}", headers=c["ih"]).json()[0]
    assert e["status"] == "resolved" and e["interventions"][0]["action"] == "meeting"
    assert not any(t["kind"] == "escalation" for t in client.get("/api/reviews", headers=c["ih"]).json())


def test_students_cannot_intervene(client):
    c = ctx(client)
    r = client.post("/api/interventions", headers=c["sh"], json={"student_id": c["student"].id, "course_id": c["cid"], "action": "note", "reason": "I approve myself"})
    assert r.status_code == 403
