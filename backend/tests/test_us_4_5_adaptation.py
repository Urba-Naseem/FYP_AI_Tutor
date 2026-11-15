"""US-4.5 Adapt the path after assessment."""
from app.services.adaptation import decide

from .helpers import make_items, published_diagnostic, take_diagnostic, take_quiz


def ctx(client, correct_clos=()):
    c = published_diagnostic(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=12)
    take_diagnostic(client, c, correct_clos=correct_clos)
    c["cid"] = c["course"]["id"]
    return c


def path(client, c):
    return client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()


def steps_for(p, code):
    return [(s["step_type"], s["status"]) for s in p["steps"] if s["clo_code"] == code]


def test_decision_rules_are_stable():
    assert decide(True, 90, "practice", 0) == ("A-ADVANCE", "advance")
    assert decide(False, 50, "review", 0) == ("A-REVIEW-FAIL", "review_fail")
    assert decide(False, 50, "practice", 1) == ("A-REPEAT", "repeat")
    assert decide(False, 50, "practice", 2) == ("A-REMEDIAL", "remedial")
    assert decide(False, 20, "practice", 0) == ("A-REMEDIAL", "remedial")
    assert decide(False, 20, "practice", 0) == decide(False, 20, "practice", 0)


def test_advance_when_attained(client):
    c = ctx(client)
    p = path(client, c)
    step = next(s for s in p["steps"] if s["clo_code"] == "CLO3" and s["step_type"] == "practice")
    r = take_quiz(client, c["sh"], c["cid"], step["clo_id"], 5, step["id"])
    assert r["adaptation"]["decision"] == "advance"
    p2 = path(client, c)
    assert ("practice", "done") in steps_for(p2, "CLO3") and p2["version_no"] == p["version_no"] + 1


def test_repeat_when_close(client):
    c = ctx(client)
    step = next(s for s in path(client, c)["steps"] if s["clo_code"] == "CLO3" and s["step_type"] == "practice")
    r = take_quiz(client, c["sh"], c["cid"], step["clo_id"], 2, step["id"])  # 40%
    assert r["adaptation"]["rule"] == "A-REPEAT"
    assert steps_for(path(client, c), "CLO3")[-2:] == [("practice", "done"), ("practice", "pending")]


def test_remedial_after_low_score_inserts_reteach(client):
    c = ctx(client)
    step = next(s for s in path(client, c)["steps"] if s["clo_code"] == "CLO3" and s["step_type"] == "practice")
    r = take_quiz(client, c["sh"], c["cid"], step["clo_id"], 1, step["id"])  # 20%
    assert r["adaptation"]["rule"] == "A-REMEDIAL"
    assert ("reteach", "pending") in steps_for(path(client, c), "CLO3")


def test_failed_quick_check_adds_study(client):
    c = ctx(client, correct_clos={"CLO1"})
    step = next(s for s in path(client, c)["steps"] if s["clo_code"] == "CLO1")
    assert step["step_type"] == "review"
    r = take_quiz(client, c["sh"], c["cid"], step["clo_id"], 1, step["id"])
    assert r["adaptation"]["rule"] == "A-REVIEW-FAIL"
    assert steps_for(path(client, c), "CLO1") == [("review", "done"), ("learn", "pending"), ("practice", "pending")]


def test_decisions_logged_with_inputs_and_explained(client):
    c = ctx(client)
    step = next(s for s in path(client, c)["steps"] if s["clo_code"] == "CLO2" and s["step_type"] == "practice")
    take_quiz(client, c["sh"], c["cid"], step["clo_id"], 2, step["id"])
    d = client.get(f"/api/paths/me/{c['cid']}/decisions", headers=c["sh"]).json()[0]
    assert d["inputs"]["mastery_pct"] == 40 and d["inputs"]["threshold"] == 60 and d["inputs"]["rules"] == "adapt-rules/v1"
    assert path(client, c)["reason"].startswith("A-REPEAT")
    assert client.get(f"/api/paths/course/{c['cid']}/student/{c['student'].id}/decisions", headers=c["ih"]).json()


def test_quiz_outside_path_still_adapts(client):
    c = ctx(client)
    clo3 = next(o for o in c["course"]["clos"] if o["code"] == "CLO3")
    r = take_quiz(client, c["sh"], c["cid"], clo3["id"], 5)
    assert r["adaptation"]["decision"] == "advance"
