"""US-4.4 Override, lock and restore learning paths."""
from .helpers import make_items, published_diagnostic, take_diagnostic, take_quiz


def ctx(client):
    c = published_diagnostic(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=12)
    take_diagnostic(client, c)
    c["cid"], c["sid"] = c["course"]["id"], c["student"].id
    c["base"] = f"/api/paths/course/{c['cid']}/student/{c['sid']}"
    return c


def test_lock_blocks_automatic_adaptation(client):
    c = ctx(client)
    assert client.post(f"{c['base']}/lock", headers=c["ih"], json={"reason": "short"}).status_code == 422
    p = client.post(f"{c['base']}/lock", headers=c["ih"], json={"reason": "Following an agreed tutoring plan"}).json()
    assert p["locked"] is True
    clo = c["course"]["clos"][1]
    r = take_quiz(client, c["sh"], c["cid"], clo["id"], 0)
    assert r["adaptation"]["rule"] == "A-LOCKED"
    after = client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()
    assert after["version_no"] == p["version_no"] and after["lock_reason"] == "Following an agreed tutoring plan"
    client.post(f"{c['base']}/unlock", headers=c["ih"], json={"reason": "Plan finished, back to automatic"})
    r = take_quiz(client, c["sh"], c["cid"], clo["id"], 0)
    assert r["adaptation"]["rule"] != "A-LOCKED"


def test_override_reorders_and_adds_but_keeps_coverage(client):
    c = ctx(client)
    p = client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()
    ids = [s["id"] for s in p["steps"]]
    reordered = [{"step_id": i} for i in reversed(ids)] + [{"clo_id": c["course"]["clos"][0]["id"], "step_type": "practice"}]
    r = client.put(f"{c['base']}/steps", headers=c["ih"], json={"steps": reordered, "reason": "Student prefers to start with recursion"})
    assert r.status_code == 200
    np_ = r.json()
    assert np_["trigger"] == "override" and np_["steps"][-1]["rule"] == "I-OVERRIDE" and len(np_["steps"]) == len(ids) + 1
    drop = np_["steps"][0]["clo_code"]
    bad = [{"step_id": s["id"]} for s in np_["steps"] if s["clo_code"] != drop]
    r = client.put(f"{c['base']}/steps", headers=c["ih"], json={"steps": bad, "reason": "Trying to drop an outcome"})
    assert r.status_code == 422 and drop in r.json()["detail"]


def test_restore_previous_version(client):
    c = ctx(client)
    v1 = client.get(f"/api/paths/me/{c['cid']}", headers=c["sh"]).json()
    take_quiz(client, c["sh"], c["cid"], c["course"]["clos"][2]["id"], 0)  # automatic change -> v2
    vers = client.get(f"{c['base']}/versions", headers=c["ih"]).json()
    assert [v["version_no"] for v in vers] == [2, 1] and vers[1]["status"] == "superseded"
    r = client.post(f"{c['base']}/restore/1", headers=c["ih"], json={"reason": "Automatic remedial step not appropriate"}).json()
    assert r["version_no"] == 3 and r["restored_from"] == 1 and len(r["steps"]) == len(v1["steps"])
    assert client.post(f"{c['base']}/restore/3", headers=c["ih"], json={"reason": "already the active version"}).status_code == 409


def test_regenerate_and_audit(client):
    c = ctx(client)
    client.post(f"{c['base']}/regenerate", headers=c["ih"], json={"reason": "Rebuild after diagnostic correction"})
    acts = {e["action"] for e in client.get("/api/audit?action=path.", headers=c["ah"]).json()["items"]}
    assert "path.regenerated" in acts


def test_students_cannot_override(client):
    c = ctx(client)
    assert client.post(f"{c['base']}/lock", headers=c["sh"], json={"reason": "I lock my own path"}).status_code == 403
