"""NB-2 Ingest and manage approved teaching sources."""
from .helpers import SOURCES_DIR, approve_curriculum, as_role, login, make_user, setup_offering

NOTES = (SOURCES_DIR / "CS101_lecture_notes.md").read_bytes()


def ctx(client):
    """Admin sets up CS101 with two instructors: one uploads (ih), the other reviews (rh)."""
    _, ah = as_role(client, "admin")
    courses = approve_curriculum(client, ah)
    inst, reviewer = make_user("instructor"), make_user("instructor")
    setup_offering(client, ah, courses["CS101"]["id"], inst)
    setup_offering(client, ah, courses["CS101"]["id"], reviewer)
    return ah, login(client, inst.email), login(client, reviewer.email), courses


def up(client, h, course_id, data=NOTES, **extra):
    return client.post("/api/sources/upload", headers=h, data={"course_id": course_id, "title": "CS101 notes", "kind": "lecture_notes", **extra},
                       files={"file": ("notes.md", data)})


def test_instructor_uploads_source_for_own_course_only(client):
    ah, ih, rh, courses = ctx(client)
    r = up(client, ih, courses["CS101"]["id"])
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["chunks"] > 3
    assert up(client, ih, courses["CS201"]["id"]).status_code == 403


def test_pending_sources_are_not_used_for_retrieval(client):
    ah, ih, rh, courses = ctx(client)
    sid = up(client, ih, courses["CS101"]["id"]).json()["id"]
    cid = courses["CS101"]["id"]
    assert client.get(f"/api/sources/search?course_id={cid}&q=recursion base case", headers=ih).json() == []
    client.post(f"/api/sources/{sid}/review", headers=rh, json={"decision": "approve", "note": "Checked"})
    hits = client.get(f"/api/sources/search?course_id={cid}&q=recursion base case", headers=ih).json()
    assert hits and "base case" in hits[0]["text"]


def test_uploader_cannot_approve_own_source(client):
    ah, ih, rh, courses = ctx(client)
    sid = up(client, ih, courses["CS101"]["id"]).json()["id"]
    assert client.post(f"/api/sources/{sid}/review", headers=ih, json={"decision": "approve"}).status_code == 409


def test_sole_instructor_may_approve_own_source_and_it_is_audited(client):
    _, ah = as_role(client, "admin")
    courses = approve_curriculum(client, ah)
    inst = make_user("instructor")
    setup_offering(client, ah, courses["CS101"]["id"], inst)
    ih = login(client, inst.email)
    sid = up(client, ih, courses["CS101"]["id"]).json()["id"]
    r = client.post(f"/api/sources/{sid}/review", headers=ih, json={"decision": "approve", "note": "Only instructor"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    ev = client.get("/api/audit?action=sources.approved", headers=ah).json()["items"][0]
    assert ev["details"]["self_approved"] is True


def test_admin_cannot_manage_sources(client):
    ah, ih, rh, courses = ctx(client)
    sid = up(client, ih, courses["CS101"]["id"]).json()["id"]
    assert up(client, ah, courses["CS101"]["id"]).status_code == 403
    assert client.post(f"/api/sources/{sid}/review", headers=ah, json={"decision": "approve", "note": "x"}).status_code == 403


def test_reject_requires_reason(client):
    ah, ih, rh, courses = ctx(client)
    sid = up(client, ih, courses["CS101"]["id"]).json()["id"]
    assert client.post(f"/api/sources/{sid}/review", headers=rh, json={"decision": "reject"}).status_code == 422
    assert client.post(f"/api/sources/{sid}/review", headers=rh, json={"decision": "reject", "note": "Copyright unclear"}).json()["status"] == "rejected"


def test_new_version_retires_old_on_approval(client):
    ah, ih, rh, courses = ctx(client)
    cid = courses["CS101"]["id"]
    v1 = up(client, ih, cid).json()
    client.post(f"/api/sources/{v1['id']}/review", headers=rh, json={"decision": "approve", "note": "ok"})
    v2 = up(client, ih, cid, NOTES + b"\n\n## Errata\nCorrected example.\n", replaces_id=v1["id"]).json()
    assert v2["version_no"] == 2
    client.post(f"/api/sources/{v2['id']}/review", headers=rh, json={"decision": "approve", "note": "ok"})
    statuses = {s["version_no"]: s["status"] for s in client.get(f"/api/sources?course_id={cid}", headers=ah).json()}
    assert statuses == {2: "approved", 1: "retired"}


def test_retire_removes_from_grounding_and_is_audited(client):
    ah, ih, rh, courses = ctx(client)
    cid = courses["CS101"]["id"]
    sid = up(client, ih, cid).json()["id"]
    client.post(f"/api/sources/{sid}/review", headers=rh, json={"decision": "approve", "note": "ok"})
    client.post(f"/api/sources/{sid}/retire", headers=rh, json={"reason": "Outdated edition"})
    assert client.get(f"/api/sources/search?course_id={cid}&q=loop", headers=ah).json() == []
    acts = {e["action"] for e in client.get("/api/audit?action=sources", headers=ah).json()["items"]}
    assert {"sources.uploaded", "sources.approved", "sources.retired"} <= acts


def test_duplicate_and_invalid_uploads(client):
    ah, ih, rh, courses = ctx(client)
    cid = courses["CS101"]["id"]
    up(client, ih, cid)
    assert up(client, ih, cid).status_code == 409
    assert client.post("/api/sources/upload", headers=ih, data={"course_id": cid, "title": "x"}, files={"file": ("x.exe", b"MZ")}).status_code == 422


def test_students_cannot_access_sources(client):
    ah, ih, rh, courses = ctx(client)
    _, sh = as_role(client, "student")
    assert client.get("/api/sources", headers=sh).status_code == 403
