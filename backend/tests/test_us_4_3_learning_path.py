"""US-4.3 Generate and explain the learning path."""
from .helpers import active_course_with_student, published_diagnostic, take_diagnostic


def test_path_built_after_diagnostic_covers_all_clos(client):
    c = published_diagnostic(client)
    take_diagnostic(client, c, correct_clos={"CLO1", "CLO2"})
    p = client.get(f"/api/paths/me/{c['course']['id']}", headers=c["sh"]).json()
    assert p["covers_all_clos"] is True and p["missing_clos"] == [] and p["trigger"] == "diagnostic"
    by_clo = {}
    for s in p["steps"]:
        by_clo.setdefault(s["clo_code"], []).append(s["step_type"])
    # ready CLO: quick check only; not-ready CLO: foundations, one study step per course topic, then the quiz
    assert by_clo["CLO1"] == ["review"]
    assert by_clo["CLO3"][0] == "remedial" and by_clo["CLO3"][-1] == "practice"
    assert set(by_clo["CLO3"][1:-1]) == {"learn"} and len(by_clo["CLO3"]) >= 3


def test_sequence_follows_unit_order(client):
    c = published_diagnostic(client)
    take_diagnostic(client, c)
    p = client.get(f"/api/paths/me/{c['course']['id']}", headers=c["sh"]).json()
    nums = [s["unit_number"] for s in p["steps"]]
    assert nums == sorted(nums)


def test_every_step_is_explained(client):
    c = published_diagnostic(client)
    take_diagnostic(client, c, correct_clos={"CLO1"})
    p = client.get(f"/api/paths/me/{c['course']['id']}", headers=c["sh"]).json()
    assert all(len(s["rationale"]) > 20 and s["rule"].startswith("R-") for s in p["steps"])
    # readiness and the attainment criterion are used in the reasons; the weakest CLO of a unit leads it
    ready_step = next(s for s in p["steps"] if s["clo_code"] == "CLO1")
    assert "100%" in ready_step["rationale"] and "60%" in ready_step["rationale"]
    assert "weakest outcome in this unit" in p["steps"][0]["rationale"]
    assert "ready" in p["summary"] and "path-rules/v2" in p["summary"]


def test_path_without_diagnostic(client):
    c = active_course_with_student(client)
    p = client.post(f"/api/paths/me/{c['course']['id']}/generate", headers=c["sh"]).json()
    # no diagnostic: every topic of every CLO is studied, then each CLO's quiz
    n_clos = len(c["course"]["clos"])
    assert p["trigger"] == "no_diagnostic" and p["covers_all_clos"]
    assert sum(s["step_type"] == "practice" for s in p["steps"]) == n_clos and sum(s["step_type"] == "learn" for s in p["steps"]) >= 12
    assert client.post(f"/api/paths/me/{c['course']['id']}/generate", headers=c["sh"]).status_code == 409


def test_instructor_can_view_student_path(client):
    c = published_diagnostic(client)
    take_diagnostic(client, c)
    p = client.get(f"/api/paths/course/{c['course']['id']}/student/{c['student'].id}", headers=c["ih"]).json()
    assert p["student_id"] == c["student"].id


def test_path_generation_logged(client):
    c = published_diagnostic(client)
    take_diagnostic(client, c)
    ev = client.get("/api/audit?action=learn.path_generated", headers=c["ah"]).json()
    assert ev["total"] == 1 and ev["items"][0]["details"]["rules"] == "path-rules/v2"
