"""US-13.1 Provide the instructor console."""
from .helpers import login, make_items, make_user, published_diagnostic, take_diagnostic, take_quiz


def ctx(client):
    c = published_diagnostic(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=15)
    take_diagnostic(client, c, correct_clos={"CLO1"})
    c["cid"] = c["course"]["id"]
    return c


def test_overview_shows_attainment_reviews_and_interventions(client):
    c = ctx(client)
    clo2 = next(o for o in c["course"]["clos"] if o["code"] == "CLO2")
    clo1 = next(o for o in c["course"]["clos"] if o["code"] == "CLO1")
    take_quiz(client, c["sh"], c["cid"], clo1["id"], 5)
    take_quiz(client, c["sh"], c["cid"], clo2["id"], 1)
    take_quiz(client, c["sh"], c["cid"], clo2["id"], 1)
    # a pending AI review for this course
    client.post("/api/items/generate", headers=c["ih"], json={"course_id": c["cid"], "count": 1})
    ov = client.get("/api/console/overview", headers=c["ih"]).json()
    assert len(ov) == 1
    o = ov[0]
    att = {a["clo_code"]: a for a in o["attainment"]}
    assert att["CLO1"]["attained_pct"] == 100 and att["CLO2"]["attained"] == 0 and att["CLO3"]["assessed"] == 0
    assert o["enrolled"] == 1 and o["diagnostic_completed"] == 1 and o["needs_intervention"] == 1


def test_course_detail_matrix_and_flags(client):
    c = ctx(client)
    clo2 = next(o for o in c["course"]["clos"] if o["code"] == "CLO2")
    take_quiz(client, c["sh"], c["cid"], clo2["id"], 1)
    take_quiz(client, c["sh"], c["cid"], clo2["id"], 1)
    d = client.get(f"/api/console/course/{c['cid']}", headers=c["ih"]).json()
    row = d["matrix"][0]
    assert row["clos"]["CLO2"]["attained"] is False and row["path"]["total"] > 0
    flags = d["needs_intervention"][0]["flags"]
    assert any(f["type"] == "below_after_attempts" and f["clo_code"] == "CLO2" for f in flags)


def test_student_detail_gives_intervention_context(client):
    c = ctx(client)
    s = client.get(f"/api/console/course/{c['cid']}/student/{c['student'].id}", headers=c["ih"]).json()
    assert s["student"]["name"] and s["diagnostic"] and s["path"]["steps"] and s["recent_activity"]


def test_console_scoped_to_assigned_courses(client):
    c = ctx(client)
    other = make_user("instructor")
    oh = login(client, other.email)
    assert client.get("/api/console/overview", headers=oh).json() == []
    assert client.get(f"/api/console/course/{c['cid']}", headers=oh).status_code == 403
    assert client.get("/api/console/overview", headers=c["sh"]).status_code == 403


def test_inactive_learners_listed_as_low_priority(client):
    c = ctx(client)
    s2 = make_user("student")
    client.post("/api/enrollment", headers=login(client, s2.email), json={"section_id": c["section"]["id"]})
    d = client.get(f"/api/console/course/{c['cid']}", headers=c["ih"]).json()
    entry = next(x for x in d["needs_intervention"] if x["student_id"] == s2.id)
    assert entry["priority"] == "low" and entry["flags"][0]["type"] == "inactive"
